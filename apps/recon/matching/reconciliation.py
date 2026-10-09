"""``build_reconciliation``: runs every matching and reporting step in order."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import pandas as pd

from ..duplicates import (
    AMOUNT_CENTS,
    combine_duplicate_reports,
    finalize_review_dispositions,
    MIN_IDENTITY_FIELDS,
    screen_duplicates,
    SOURCE_POS,
)
from ..fuzzy_po_matching import FUZZY_PO_CONFIDENCE
from ..vendor_aliases import VendorAlias
from .core import (
    _amount_total,
    _fingerprint_columns,
    _gross_amount_total,
    _historical_row_indexes,
    _identity_columns,
    _matched_row_indexes,
    _split_reference_ids,
    cents_to_float,
    INF_ID,
    QB_ID,
    ReconciliationResult,
)
from .evidence import FINDING_CONFLICTING_LINKS
from .export_checks import assert_exportable
from .engine import perform_matching, prepare_working_frame
from .exceptions import (
    build_ambiguous_duplicate_candidates,
    build_po_reuse_errors,
    build_reference_amount_variances,
    po_reuse_error_qb_index_map,
)
from .references import (
    add_duplicate_report_references,
    assign_match_references,
    refine_candidates_with_match_references,
)
from .holds import build_reference_evidence_review_holds
from .dispositions import (
    _final_disposition_metrics,
    build_fuzzy_match_review_holds,
    build_historical_clearances,
    build_qb_dispositions,
)
from .paired_rows import build_match_assessments, build_paired_rows
from .summaries import (
    build_controls,
    build_customer_summary,
    build_exception_analysis,
    build_method_summary,
    build_product_review_items,
    build_product_summary,
    customer_cases_without_bottle_count,
)
from .validation import validate_reconciliation


def build_reconciliation(
    qb_raw: pd.DataFrame,
    inf_raw: pd.DataFrame,
    qb_mapping: dict[str, Optional[str]],
    inf_mapping: dict[str, Optional[str]],
    metadata: dict[str, Any],
    fiscal_year: int,
    qb_secondary_raw: Optional[pd.DataFrame] = None,
    inf_secondary_raw: Optional[pd.DataFrame] = None,
    qb_secondary_mapping: Optional[dict[str, Optional[str]]] = None,
    inf_secondary_mapping: Optional[dict[str, Optional[str]]] = None,
    vendor_aliases: Optional[list[VendorAlias]] = None,
) -> ReconciliationResult:
    # Confirmed vendor aliases (see vendor_aliases.py) are supplied by the
    # caller rather than auto-loaded from disk here -- this function stays
    # a pure computation over its arguments (no hidden file I/O, and no
    # risk of the standing alias store silently changing behavior for a
    # caller, including a test, that didn't ask for it). The application
    # entry point loads the standing store and passes it in explicitly.
    qb = prepare_working_frame(qb_raw, qb_mapping, "QB", fiscal_year)
    inf = prepare_working_frame(inf_raw, inf_mapping, "INF", fiscal_year)
    qb_secondary = (
        prepare_working_frame(
            qb_secondary_raw, qb_secondary_mapping, "QB", fiscal_year,
            id_prefix="QB-HIST",
        )
        if qb_secondary_raw is not None and qb_secondary_mapping is not None
        else None
    )
    inf_secondary = (
        prepare_working_frame(
            inf_secondary_raw, inf_secondary_mapping, "INF", fiscal_year,
            id_prefix="INF-HIST",
        )
        if inf_secondary_raw is not None and inf_secondary_mapping is not None
        else None
    )

    # Canonicalize strong same-file groups before matching. Only excess copies
    # are removed; the earliest source-position row remains active. Weak groups
    # are reported for review without automatic exclusion. Historical frames
    # are additionally compared with their own primary population so an
    # overlapping historical row cannot clear an opposing primary exception.
    # A CONFIRMED QuickBooks exact duplicate -- identical normalized PO, invoice,
    # and signed cents, plus a shared line-level source ID or a sufficient
    # identical fingerprint of the explicit stable line-level fields (customer,
    # date, item, quantity, rate) -- is decided BEFORE any matching: the earliest row
    # is the canonical one and every excess copy is excluded from matching and
    # from the proposed JE. Rows that share the key but are not confirmed copies
    # (e.g. two real sales with different customers or dates), and the weaker
    # PO-only / invoice-only groups, stay fully active through matching; the
    # post-matching accounting below decides what happens to them.
    qb_screen = screen_duplicates(
        qb, QB_ID, "QuickBooks", "Primary",
        "A confirmed exact QuickBooks duplicate (same PO, invoice, and signed cents AND a shared "
        "line-level source ID or a sufficient identical stable-field fingerprint) keeps one canonical row; every excess "
        "copy is excluded before matching and from the proposed JE. A row that shares only the "
        "key is a potential duplicate: it stays active for matching and, if it does not match, "
        "is held for review -- see the post-matching duplicate accounting.",
        auto_exclude_strict=True,
        fingerprint_columns=_fingerprint_columns(qb_mapping),
        # The same standard of evidence applies to EVERY dataset: an exclusion
        # needs a trusted line-level ID or sufficient line-identity evidence --
        # never quantity alone -- or the sibling is held for review. (Even a
        # non-JE exclusion changes the candidate pool the QuickBooks passes see.)
        identity_columns=_identity_columns(qb_mapping),
        min_identity_fields=MIN_IDENTITY_FIELDS,
    )
    inf_screen = screen_duplicates(
        inf, INF_ID, "Infinium", "Primary",
        "Strong groups retain one canonical row; only excess copies are excluded from matching.",
        auto_exclude_strict=True,
        fingerprint_columns=_fingerprint_columns(inf_mapping),
        identity_columns=_identity_columns(inf_mapping),
        min_identity_fields=MIN_IDENTITY_FIELDS,
    )
    qb_secondary_screen = (
        screen_duplicates(
            qb_secondary, QB_ID, "QuickBooks", "Historical (Secondary)",
            "Excess copies and rows overlapping QuickBooks primary are excluded from clearance.",
            auto_exclude_strict=True,
            fingerprint_columns=_fingerprint_columns(qb_secondary_mapping),
            identity_columns=_identity_columns(qb_secondary_mapping),
            min_identity_fields=MIN_IDENTITY_FIELDS,
            reference_frame=qb_screen.active_frame,
            reference_id_column=QB_ID,
        )
        if qb_secondary is not None else None
    )
    inf_secondary_screen = (
        screen_duplicates(
            inf_secondary, INF_ID, "Infinium", "Historical (Secondary)",
            "Excess copies and rows overlapping Infinium primary are excluded from clearance.",
            auto_exclude_strict=True,
            fingerprint_columns=_fingerprint_columns(inf_secondary_mapping),
            identity_columns=_identity_columns(inf_secondary_mapping),
            min_identity_fields=MIN_IDENTITY_FIELDS,
            reference_frame=inf_screen.active_frame,
            reference_id_column=INF_ID,
        )
        if inf_secondary is not None else None
    )

    qb_active = qb_screen.active_frame
    inf_active = inf_screen.active_frame
    # A historical review candidate is never allowed to clear a primary
    # exception. Unlike primary candidates, historical rows are optional
    # evidence rather than accrual-source rows, so the safe treatment is to
    # withhold every unresolved candidate from clearance until a documented
    # disposition is supplied and the reconciliation is rerun.
    qb_secondary_active = (
        qb_secondary_screen.active_frame.drop(
            index=qb_secondary_screen.suspected_rows, errors="ignore"
        )
        if qb_secondary_screen else qb_secondary
    )
    inf_secondary_active = (
        inf_secondary_screen.active_frame.drop(
            index=inf_secondary_screen.suspected_rows, errors="ignore"
        )
        if inf_secondary_screen else inf_secondary
    )

    matches, initially_unmatched_qb, initially_unmatched_inf, candidates = perform_matching(
        qb_active, inf_active, vendor_aliases=vendor_aliases
    )

    # A fuzzy PO/text match is a similarity guess, not a certain relationship,
    # so it is never posted the way an exact match is. Pull every fuzzy group
    # out of the accepted matches and into its own review-hold population --
    # see build_fuzzy_match_review_holds for the reviewer-facing report.
    fuzzy_hold_groups = [group for group in matches if group.confidence == FUZZY_PO_CONFIDENCE]
    matches = [group for group in matches if group.confidence != FUZZY_PO_CONFIDENCE]
    fuzzy_hold_groups.sort(
        key=lambda group: min(qb_active.at[idx, SOURCE_POS] for idx in group.qb_rows)
    )
    for number, group in enumerate(fuzzy_hold_groups, 1):
        group.match_id = f"FUZZY-{number:06d}"
    fuzzy_match_review_hold_qb = sorted({idx for group in fuzzy_hold_groups for idx in group.qb_rows})
    fuzzy_match_review_hold_inf = sorted({idx for group in fuzzy_hold_groups for idx in group.inf_rows})
    fuzzy_match_review_hold_analysis = build_fuzzy_match_review_holds(
        qb_active, inf_active, fuzzy_hold_groups
    )

    historical_clearances, unmatched_qb, unmatched_inf = build_historical_clearances(
        qb,
        inf,
        initially_unmatched_qb,
        initially_unmatched_inf,
        qb_secondary_active,
        inf_secondary_active,
        source_files={
            "QuickBooks Secondary (Historical)": metadata.get("qb_secondary_filename"),
            "Infinium Secondary (Historical)": metadata.get("inf_secondary_filename"),
        },
    )

    # Matching and historical clearance are now final: give every accepted
    # relationship its one canonical reference (M-### one-to-one, G-### grouped)
    # before anything is sorted or rendered, and word the exceptions that
    # point at an already-consumed record with the exact match they cite.
    # Purely labeling -- no decision above or below this point changes.
    match_register, historical_clearances = assign_match_references(
        matches, historical_clearances, qb, inf,
    )
    candidates = refine_candidates_with_match_references(
        candidates, match_register, qb, inf, qb_screen.report,
    )

    # Rows that share a duplicate key but are NOT confirmed copies of one
    # transaction (different customer/date/native ID, or a weaker PO-only /
    # invoice-only key) stayed fully active through matching. Now that matching
    # and historical clearance are both final, decide -- per group -- what is
    # safe. A potential duplicate is never discarded on the key alone:
    #   * If ANY member matched, every member that did not is a potential
    #     duplicate of that matched sibling -- it may already be represented, so
    #     it is HELD for review, out of the proposed JE, not accrued.
    #   * If NO member matched, the earliest-listed member is the group's
    #     representative and stays an ordinary exception; every other member is
    #     held as a potential duplicate of it. Nothing is excluded or accrued on
    #     the strength of the key alone.
    qb_group_id_by_index: dict[int, str] = {}
    if not qb_screen.report.empty:
        qb_report_group_by_id = (
            qb_screen.report.set_index("Source Row ID")["Duplicate Group ID"].to_dict()
        )
        qb_group_id_by_index = {
            idx: qb_report_group_by_id[qb.at[idx, QB_ID]]
            for idx in qb_screen.suspected_rows
            if qb.at[idx, QB_ID] in qb_report_group_by_id
        }
    qb_duplicate_groups: dict[str, list[int]] = defaultdict(list)
    for idx, group_id in qb_group_id_by_index.items():
        qb_duplicate_groups[group_id].append(idx)

    resolved_suspected_qb_ids: set[Any] = set()
    canonical_survivor_qb_ids: set[Any] = set()
    held_canonical_qb_ids: dict[Any, Any] = {}
    duplicate_review_hold_qb: list[int] = []
    held_suspected_qb_ids: set[Any] = set()
    for members in qb_duplicate_groups.values():
        matched_members = sorted(
            (idx for idx in members if idx not in unmatched_qb), key=lambda idx: qb.at[idx, SOURCE_POS],
        )
        unmatched_members = sorted(
            (idx for idx in members if idx in unmatched_qb), key=lambda idx: qb.at[idx, SOURCE_POS],
        )
        if matched_members:
            representative_id = qb.at[matched_members[0], QB_ID]
            resolved_suspected_qb_ids.update(qb.at[idx, QB_ID] for idx in matched_members)
            held = unmatched_members
        elif unmatched_members:
            representative_id = qb.at[unmatched_members[0], QB_ID]
            canonical_survivor_qb_ids.add(representative_id)
            held = unmatched_members[1:]
        else:
            continue
        duplicate_review_hold_qb.extend(held)
        for idx in held:
            held_suspected_qb_ids.add(qb.at[idx, QB_ID])
            held_canonical_qb_ids[qb.at[idx, QB_ID]] = representative_id
    # Only CONFIRMED exact duplicates are excluded, and all of them were
    # excluded before matching -- this is the only source of excluded QB rows.
    duplicate_qb_rows_final = sorted(set(qb_screen.duplicate_rows))

    duplicate_review_hold_qb = sorted(set(duplicate_review_hold_qb))
    unmatched_qb = sorted(set(unmatched_qb) - set(duplicate_review_hold_qb))

    # Infinium carries no accrual effect of its own, so its weak-basis
    # duplicate candidates keep the original policy: stay active through
    # matching, and anything still unresolved afterward is held for review
    # rather than silently assumed one way or the other.
    duplicate_review_hold_inf = sorted(set(unmatched_inf) & set(inf_screen.suspected_rows))
    unmatched_inf = sorted(set(unmatched_inf) - set(duplicate_review_hold_inf))
    resolved_suspected_inf_ids = {
        inf.at[idx, INF_ID] for idx in inf_screen.suspected_rows if idx not in duplicate_review_hold_inf
    }
    held_suspected_inf_ids = {inf.at[idx, INF_ID] for idx in duplicate_review_hold_inf}

    # Final unresolved rows are now examined for mutually unique reference
    # relationships with differing amounts. These rows are not ordinary
    # missing-record accruals: both sides are withheld from automatic posting
    # and itemized in their own auditor-facing review population.
    # Rows whose PO and invoice point at different Infinium transactions are
    # held as conflicting identifier links (see the candidate table). They are
    # kept out of the variance / ambiguity classifiers below: those would
    # otherwise pair the row with whichever link they happen to see first.
    conflict_qb: set[int] = set()
    conflict_inf: set[int] = set()
    if not candidates.empty:
        conflict_ids = set(
            candidates.loc[candidates["Evidence Finding"] == FINDING_CONFLICTING_LINKS, "QuickBooks Row ID"]
        )
        conflict_qb = {idx for idx in unmatched_qb if qb.at[idx, QB_ID] in conflict_ids}
        conflicting_inf_ids = {
            piece
            for text in candidates.loc[candidates["QuickBooks Row ID"].isin(conflict_ids), "Conflicting Infinium IDs"]
            for piece in _split_reference_ids(text)
        }
        conflict_inf = {idx for idx in unmatched_inf if inf.at[idx, INF_ID] in conflicting_inf_ids}

    (
        amount_variance_analysis,
        amount_variance_review_hold_qb,
        amount_variance_review_hold_inf,
    ) = build_reference_amount_variances(
        qb, inf,
        [idx for idx in unmatched_qb if idx not in conflict_qb],
        [idx for idx in unmatched_inf if idx not in conflict_inf],
    )
    unmatched_qb = sorted(
        set(unmatched_qb).difference(amount_variance_review_hold_qb)
    )
    unmatched_inf = sorted(
        set(unmatched_inf).difference(amount_variance_review_hold_inf)
    )

    # Whatever is still unresolved on the QuickBooks side after the clean,
    # mutually unique amount-variance pass above may still share a PO
    # and/or invoice with more than one remaining Infinium row -- an
    # ambiguous duplicate rather than a single traceable reference. These
    # are likewise withheld from accrual rather than posted as an ordinary
    # exception; see build_ambiguous_duplicate_candidates.
    ambiguous_duplicate_analysis, ambiguous_duplicate_qb = build_ambiguous_duplicate_candidates(
        qb, inf,
        [idx for idx in unmatched_qb if idx not in conflict_qb],
        [idx for idx in unmatched_inf if idx not in conflict_inf],
    )
    unmatched_qb = sorted(
        set(unmatched_qb).difference(ambiguous_duplicate_qb)
    )

    # A normalized PO reused across 2+ rows still remaining in the
    # QuickBooks accrual population whose grouped total does not tie
    # exactly to the grouped Infinium total for that PO -- see
    # build_po_reuse_errors. Unlike every review-hold population above,
    # these rows are NOT removed from unmatched_qb: they stay in the
    # accrual exactly as an ordinary exception would, just with their own
    # traceable classification and grouped detail.
    po_reuse_errors = build_po_reuse_errors(qb, inf, unmatched_qb, unmatched_inf)

    # Anything still "unmatched" that nevertheless has evidence of an Infinium
    # counterpart -- a PO/invoice an accepted match already consumed, a reused PO,
    # a single reference candidate whose amount differs, an exact candidate that is
    # not uniquely available, several controlled-typo candidates, a historical
    # record withheld by an unresolved duplicate, an unreadable amount -- cannot
    # be called a genuine missing transaction: "I cannot safely match this" is not
    # "this does not exist in Infinium". Those rows are held out of the proposed JE.
    # Only a row with NO support anywhere stays in the JE population.
    withheld_inf_secondary = (
        inf_secondary.loc[inf_secondary_screen.suspected_rows]
        if inf_secondary is not None and inf_secondary_screen is not None
        and inf_secondary_screen.suspected_rows else None
    )
    withheld_group_by_id = (
        dict(zip(inf_secondary_screen.report["Source Row ID"], inf_secondary_screen.report["Duplicate Group ID"]))
        if inf_secondary_screen is not None and not inf_secondary_screen.report.empty else {}
    )
    reference_hold_analysis, reference_hold_qb = build_reference_evidence_review_holds(
        qb, inf, candidates, unmatched_qb, match_register,
        po_reuse_errors, withheld_inf_secondary, withheld_group_by_id,
    )
    unmatched_qb = sorted(set(unmatched_qb).difference(reference_hold_qb))

    # Finalized (post-matching) duplicate reports are computed here, before
    # build_paired_rows, because the QuickBooks accounting above now makes a
    # genuinely post-matching decision (excess vs. ordinary exception) that
    # qb_screen.report -- fixed at pre-matching screening time -- cannot
    # reflect; paired_rows and the audit report must both read the same,
    # final disposition rather than the two silently disagreeing.
    qb_primary_duplicate_report = finalize_review_dispositions(
        qb_screen.report,
        resolved_ids=resolved_suspected_qb_ids,
        held_ids=held_suspected_qb_ids,
        canonical_survivor_ids=canonical_survivor_qb_ids,
        held_canonical_ids=held_canonical_qb_ids,
    )
    inf_primary_duplicate_report = finalize_review_dispositions(
        inf_screen.report,
        resolved_ids=resolved_suspected_inf_ids,
        held_ids=held_suspected_inf_ids,
    )

    paired_rows = build_paired_rows(
        matches, historical_clearances, unmatched_qb, unmatched_inf, qb, inf, candidates,
        duplicate_qb_rows_final, inf_screen.duplicate_rows,
        duplicate_review_hold_qb, duplicate_review_hold_inf,
        amount_variance_analysis,
        ambiguous_duplicate_analysis,
        po_reuse_errors,
        fuzzy_hold_groups,
        qb_primary_duplicate_report,
        inf_primary_duplicate_report,
        match_register,
        reference_hold_analysis,
    )
    qb_dispositions = build_qb_dispositions(
        paired_rows, qb, set(po_reuse_error_qb_index_map(po_reuse_errors)), match_register,
        candidates=candidates,
    )
    assessments = build_match_assessments(
        matches, historical_clearances, unmatched_qb, qb, inf, candidates,
        amount_variance_analysis,
        ambiguous_duplicate_analysis,
        reference_hold_analysis,
    )
    method_summary = build_method_summary(
        matches, historical_clearances, unmatched_qb, unmatched_inf, qb, inf,
        duplicate_qb_rows_final, inf_screen.duplicate_rows,
        duplicate_review_hold_qb, duplicate_review_hold_inf,
        amount_variance_review_hold_qb, amount_variance_review_hold_inf,
        ambiguous_duplicate_qb,
        fuzzy_match_review_hold_qb, fuzzy_match_review_hold_inf,
        reference_hold_qb,
    )
    exception_analysis = build_exception_analysis(
        qb, inf, unmatched_qb, unmatched_inf, candidates,
        amount_variance_analysis, paired_rows,
        fuzzy_match_review_hold_analysis,
        ambiguous_duplicate_analysis,
        reference_hold_analysis,
    )
    qb_secondary_duplicate_report = (
        finalize_review_dispositions(
            qb_secondary_screen.report,
            historical_hold_ids={
                qb_secondary.at[idx, QB_ID]
                for idx in qb_secondary_screen.suspected_rows
            },
        )
        if qb_secondary_screen else None
    )
    duplicate_analysis = add_duplicate_report_references(
        combine_duplicate_reports(qb_primary_duplicate_report, qb_secondary_duplicate_report),
        match_register,
    )
    inf_secondary_duplicate_report = (
        finalize_review_dispositions(
            inf_secondary_screen.report,
            historical_hold_ids={
                inf_secondary.at[idx, INF_ID]
                for idx in inf_secondary_screen.suspected_rows
            },
        )
        if inf_secondary_screen else None
    )
    infinium_duplicate_analysis = add_duplicate_report_references(
        combine_duplicate_reports(inf_primary_duplicate_report, inf_secondary_duplicate_report),
        match_register,
    )
    product_summary = build_product_summary(
        qb,
        qb_mapping,
        metadata.get("fiscal_period"),
        fiscal_year,
    )
    customer_summary = build_customer_summary(
        qb,
        qb_mapping,
        metadata.get("fiscal_period"),
        fiscal_year,
    )
    product_review_items = build_product_review_items(
        qb,
        qb_mapping,
        metadata.get("fiscal_period"),
        fiscal_year,
    )
    customer_cases_without_bottles = customer_cases_without_bottle_count(
        qb,
        qb_mapping,
        metadata.get("fiscal_period"),
        fiscal_year,
    )
    controls = build_controls(
        qb, inf, matches, historical_clearances, unmatched_qb, unmatched_inf,
        duplicate_qb_rows_final, inf_screen.duplicate_rows,
        duplicate_review_hold_qb, duplicate_review_hold_inf,
        amount_variance_review_hold_qb, amount_variance_review_hold_inf,
        ambiguous_duplicate_qb,
        fuzzy_match_review_hold_qb, fuzzy_match_review_hold_inf,
        reference_hold_qb,
        qb_dispositions,
    )

    matched_q = _matched_row_indexes(matches, "QB")
    matched_i = _matched_row_indexes(matches, "INF")
    historical_matched_q = _historical_row_indexes(
        historical_clearances,
        "QuickBooks Primary",
        "Primary Row Index",
    )
    historical_matched_i = _historical_row_indexes(
        historical_clearances,
        "Infinium Primary",
        "Primary Row Index",
    )
    qb_secondary_used = _historical_row_indexes(
        historical_clearances,
        "Infinium Primary",
        "Secondary Row Index",
    )
    inf_secondary_used = _historical_row_indexes(
        historical_clearances,
        "QuickBooks Primary",
        "Secondary Row Index",
    )
    qb_total_cents = _amount_total(qb, qb.index)
    inf_total_cents = _amount_total(inf, inf.index)
    unmatched_q_cents = _amount_total(qb, unmatched_qb)
    matched_q_cents = _amount_total(qb, matched_q)
    matched_i_cents = _amount_total(inf, matched_i)
    cleared_q_cents = _amount_total(qb, historical_matched_q)
    cleared_i_cents = _amount_total(inf, historical_matched_i)
    qb_gross = _gross_amount_total(qb, qb.index)
    matched_q_gross = _gross_amount_total(qb, matched_q)
    posting_blockers: list[str] = []
    if duplicate_review_hold_qb:
        posting_blockers.append("unresolved QuickBooks duplicate review holds")
    if duplicate_review_hold_inf:
        posting_blockers.append("unresolved Infinium duplicate review holds")
    if qb_secondary_screen and qb_secondary_screen.suspected_rows:
        posting_blockers.append("unresolved QuickBooks historical duplicate review holds")
    if inf_secondary_screen and inf_secondary_screen.suspected_rows:
        posting_blockers.append("unresolved Infinium historical duplicate review holds")
    if not amount_variance_analysis.empty:
        posting_blockers.append("unresolved reference-matched amount variance review holds")
    if not ambiguous_duplicate_analysis.empty:
        posting_blockers.append("unresolved ambiguous duplicate candidates")
    if not fuzzy_match_review_hold_analysis.empty:
        posting_blockers.append("unresolved fuzzy PO match review holds")
    if not reference_hold_analysis.empty:
        posting_blockers.append("unresolved reference-evidence review holds")
    if qb[AMOUNT_CENTS].isna().any():
        posting_blockers.append("invalid QuickBooks amounts")
    if inf[AMOUNT_CENTS].isna().any():
        posting_blockers.append("invalid Infinium amounts")
    automatic_duplicate_exclusions = bool(
        duplicate_qb_rows_final
        or inf_screen.duplicate_rows
        or (qb_secondary_screen and qb_secondary_screen.duplicate_rows)
        or (inf_secondary_screen and inf_secondary_screen.duplicate_rows)
    )
    if automatic_duplicate_exclusions and not bool(
        metadata.get("duplicate_source_grain_validated", False)
    ):
        posting_blockers.append(
            "source-report grain has not been documented as safe for automatic duplicate exclusion"
        )
    metrics = {
        "QuickBooks Rows": len(qb),
        "Infinium Rows": len(inf),
        "QuickBooks Source Total": cents_to_float(qb_total_cents),
        "Infinium Source Total": cents_to_float(inf_total_cents),
        "Source Difference": cents_to_float(qb_total_cents - inf_total_cents),
        "Matched QuickBooks Rows": len(matched_q) + len(historical_matched_q),
        "Matched Infinium Rows": len(matched_i) + len(historical_matched_i),
        "Matched QuickBooks Amount": cents_to_float(matched_q_cents + cleared_q_cents),
        "Matched Infinium Amount": cents_to_float(matched_i_cents + cleared_i_cents),
        "Matched Amount Difference": cents_to_float(matched_q_cents - matched_i_cents),
        "Historical QuickBooks Rows Cleared": len(historical_matched_q),
        "Historical Infinium Rows Cleared": len(historical_matched_i),
        "Historical Clearances": (
            int(historical_clearances["Clearance ID"].nunique())
            if not historical_clearances.empty else 0
        ),
        "QuickBooks Secondary Rows": len(qb_secondary) if qb_secondary is not None else 0,
        "QuickBooks Secondary Rows Used": len(qb_secondary_used),
        "QuickBooks Secondary Rows Ignored": (
            len(qb_secondary) - len(qb_secondary_used) if qb_secondary is not None else 0
        ),
        "Infinium Secondary Rows": len(inf_secondary) if inf_secondary is not None else 0,
        "Infinium Secondary Rows Used": len(inf_secondary_used),
        "Infinium Secondary Rows Ignored": (
            len(inf_secondary) - len(inf_secondary_used) if inf_secondary is not None else 0
        ),
        "Unresolved QuickBooks Rows": len(unmatched_qb),
        "Unresolved QuickBooks Amount": cents_to_float(unmatched_q_cents),
        "Unmatched Infinium Rows": len(unmatched_inf),
        "Unmatched Infinium Amount": cents_to_float(_amount_total(inf, unmatched_inf)),
        "Duplicate QuickBooks Rows": len(duplicate_qb_rows_final),
        "Duplicate QuickBooks Amount": cents_to_float(_amount_total(qb, duplicate_qb_rows_final)),
        "Duplicate Infinium Rows": len(inf_screen.duplicate_rows),
        "Duplicate Infinium Amount": cents_to_float(_amount_total(inf, inf_screen.duplicate_rows)),
        "Suspected QuickBooks Duplicate Rows": len(qb_screen.suspected_rows),
        "Suspected Infinium Duplicate Rows": len(inf_screen.suspected_rows),
        "Suspected QuickBooks Secondary Duplicate Rows": (
            len(qb_secondary_screen.suspected_rows) if qb_secondary_screen else 0
        ),
        "Suspected Infinium Secondary Duplicate Rows": (
            len(inf_secondary_screen.suspected_rows) if inf_secondary_screen else 0
        ),
        "Duplicate QuickBooks Secondary Rows Excluded": (
            len(qb_secondary_screen.duplicate_rows) if qb_secondary_screen else 0
        ),
        "Duplicate Infinium Secondary Rows Excluded": (
            len(inf_secondary_screen.duplicate_rows) if inf_secondary_screen else 0
        ),
        "Duplicate Review Hold QuickBooks Rows": len(duplicate_review_hold_qb),
        "Duplicate Review Hold QuickBooks Amount": cents_to_float(
            _amount_total(qb, duplicate_review_hold_qb)
        ),
        "Duplicate Review Hold Infinium Rows": len(duplicate_review_hold_inf),
        "Duplicate Review Hold Infinium Amount": cents_to_float(
            _amount_total(inf, duplicate_review_hold_inf)
        ),
        "Historical Duplicate Review Hold QuickBooks Rows": (
            len(qb_secondary_screen.suspected_rows) if qb_secondary_screen else 0
        ),
        "Historical Duplicate Review Hold Infinium Rows": (
            len(inf_secondary_screen.suspected_rows) if inf_secondary_screen else 0
        ),
        "Reference-Matched Amount Variance Rows": len(amount_variance_analysis),
        "Amount Variance Review Hold QuickBooks Amount": cents_to_float(
            _amount_total(qb, amount_variance_review_hold_qb)
        ),
        "Amount Variance Review Hold Infinium Amount": cents_to_float(
            _amount_total(inf, amount_variance_review_hold_inf)
        ),
        "Amount Variance Potential Net Difference": (
            float(amount_variance_analysis["Potential Difference"].sum())
            if not amount_variance_analysis.empty else 0.0
        ),
        "Amount Variance Potential Gross Difference": (
            float(amount_variance_analysis["Absolute Difference"].sum())
            if not amount_variance_analysis.empty else 0.0
        ),
        "Ambiguous Duplicate QuickBooks Rows": len(ambiguous_duplicate_qb),
        "Ambiguous Duplicate QuickBooks Amount": cents_to_float(
            _amount_total(qb, ambiguous_duplicate_qb)
        ),
        "PO Re-use Error Groups": len(po_reuse_errors),
        "PO Re-use Error QuickBooks Rows": (
            int(po_reuse_errors["QuickBooks Row Count"].sum()) if not po_reuse_errors.empty else 0
        ),
        "PO Re-use Error Net Difference": (
            float(po_reuse_errors["Difference"].sum()) if not po_reuse_errors.empty else 0.0
        ),
        "Fuzzy Match Review Hold Rows": len(fuzzy_match_review_hold_analysis),
        "Fuzzy Match Review Hold QuickBooks Amount": cents_to_float(
            _amount_total(qb, fuzzy_match_review_hold_qb)
        ),
        "Fuzzy Match Review Hold Infinium Amount": cents_to_float(
            _amount_total(inf, fuzzy_match_review_hold_inf)
        ),
        "Reference Review Hold QuickBooks Rows": len(reference_hold_analysis),
        "Reference Review Hold QuickBooks Amount": cents_to_float(_amount_total(qb, reference_hold_qb)),
        **_final_disposition_metrics(qb_dispositions),
        "Posting Blockers": "; ".join(posting_blockers) if posting_blockers else "None",
        "Posting Status": "REVIEW REQUIRED" if posting_blockers else "READY TO POST",
        "Posting Authorization": "DO NOT POST" if posting_blockers else "AUTHORIZED BY AUTOMATED CONTROLS",
        "QuickBooks Match Rate by Row": (
            (len(matched_q) + len(historical_matched_q)) / len(qb) if len(qb) else 0
        ),
        "QuickBooks Match Rate by Gross Amount": (
            (matched_q_gross + _gross_amount_total(qb, historical_matched_q))
            / qb_gross if qb_gross else 0
        ),
        "Invalid QuickBooks Amounts": int(qb[AMOUNT_CENTS].isna().sum()),
        "Invalid Infinium Amounts": int(inf[AMOUNT_CENTS].isna().sum()),
        "QuickBooks Subtotal Rows Excluded": int(metadata.get("qb_subtotal_rows_excluded", 0)),
        "Duplicate QuickBooks Item Groups": (
            int(duplicate_analysis["Duplicate Group ID"].nunique()) if not duplicate_analysis.empty else 0
        ),
        "Duplicate QuickBooks Confirmed Copy Sets": (
            int(duplicate_analysis["Confirmed Copy Set ID"].nunique())
            if not duplicate_analysis.empty else 0
        ),
        "Duplicate Infinium Item Groups": (
            int(infinium_duplicate_analysis["Duplicate Group ID"].nunique())
            if not infinium_duplicate_analysis.empty else 0
        ),
        "Duplicate Infinium Confirmed Copy Sets": (
            int(infinium_duplicate_analysis["Confirmed Copy Set ID"].nunique())
            if not infinium_duplicate_analysis.empty else 0
        ),
        "Control Status": "PASS" if controls["Status"].eq("PASS").all() else "FAIL",
    }

    result = ReconciliationResult(
        run_id=metadata["run_id"],
        run_timestamp=metadata["run_timestamp_dt"],
        qb_raw=qb_raw.copy(),
        inf_raw=inf_raw.copy(),
        qb_work=qb,
        inf_work=inf,
        matches=matches,
        paired_rows=paired_rows,
        candidates=candidates,
        assessments=assessments,
        method_summary=method_summary,
        exception_analysis=exception_analysis,
        amount_variance_analysis=amount_variance_analysis,
        duplicate_analysis=duplicate_analysis,
        infinium_duplicate_analysis=infinium_duplicate_analysis,
        product_summary=product_summary,
        customer_summary=customer_summary,
        controls=controls,
        metrics=metrics,
        qb_mapping=qb_mapping,
        inf_mapping=inf_mapping,
        metadata=metadata,
        unmatched_qb=unmatched_qb,
        unmatched_inf=unmatched_inf,
        historical_clearances=historical_clearances,
        qb_secondary_raw=qb_secondary_raw.copy() if qb_secondary_raw is not None else None,
        inf_secondary_raw=inf_secondary_raw.copy() if inf_secondary_raw is not None else None,
        qb_secondary_work=qb_secondary,
        inf_secondary_work=inf_secondary,
        qb_secondary_mapping=qb_secondary_mapping,
        inf_secondary_mapping=inf_secondary_mapping,
        duplicate_qb_rows=duplicate_qb_rows_final,
        duplicate_inf_rows=inf_screen.duplicate_rows,
        duplicate_qb_secondary_rows=qb_secondary_screen.duplicate_rows if qb_secondary_screen else [],
        duplicate_inf_secondary_rows=inf_secondary_screen.duplicate_rows if inf_secondary_screen else [],
        suspected_qb_rows=sorted(set(qb_screen.suspected_rows)),
        suspected_inf_rows=inf_screen.suspected_rows,
        suspected_qb_secondary_rows=qb_secondary_screen.suspected_rows if qb_secondary_screen else [],
        suspected_inf_secondary_rows=inf_secondary_screen.suspected_rows if inf_secondary_screen else [],
        duplicate_review_hold_qb_rows=duplicate_review_hold_qb,
        duplicate_review_hold_inf_rows=duplicate_review_hold_inf,
        amount_variance_review_hold_qb_rows=amount_variance_review_hold_qb,
        amount_variance_review_hold_inf_rows=amount_variance_review_hold_inf,
        ambiguous_duplicate_analysis=ambiguous_duplicate_analysis,
        ambiguous_duplicate_qb_rows=ambiguous_duplicate_qb,
        po_reuse_errors=po_reuse_errors,
        match_register=match_register,
        fuzzy_match_review_hold_analysis=fuzzy_match_review_hold_analysis,
        fuzzy_match_review_hold_qb_rows=fuzzy_match_review_hold_qb,
        fuzzy_match_review_hold_inf_rows=fuzzy_match_review_hold_inf,
        reference_hold_analysis=reference_hold_analysis,
        reference_hold_qb_rows=reference_hold_qb,
        qb_dispositions=qb_dispositions,
        product_review_items=product_review_items,
        customer_cases_without_bottles=customer_cases_without_bottles,
    )
    validate_reconciliation(result)
    result.export_controls = assert_exportable(result)
    return result
