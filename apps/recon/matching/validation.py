"""``validate_reconciliation``: end-of-run integrity checks over a finished result."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from ..duplicates import AMOUNT_CENTS, DUPLICATE_RULE_VERSION, NORM_INV, NORM_PO
from ..fuzzy_po_matching import FUZZY_PO_CONFIDENCE
from .core import (
    _amount_total,
    _historical_row_indexes,
    flag_mask,
    INF_ID,
    MAX_GROUP_SIZE,
    QB_ID,
    ReconciliationResult,
)
from .labels import (
    DISPOSITION_DUPLICATE_EXCLUDED,
    DISPOSITION_MATCHED,
    DISPOSITION_REVIEW_HOLD,
    DISPOSITION_TRUE_UNMATCHED,
    FINAL_DISPOSITIONS,
    REFERENCE_HOLD_SECTION,
)
from .exceptions import po_reuse_error_qb_index_map
from .references import validate_match_references


def _validate_reference_holds_and_dispositions(
    result: "ReconciliationResult", reference_hold_qb: set[int],
) -> None:
    """The reference-evidence hold population and the final-disposition ledger
    must agree exactly with every other population, and the proposed JE must
    be built from TRUE_UNMATCHED rows only."""
    unmatched = set(result.unmatched_qb)
    duplicates = set(result.duplicate_qb_rows)
    other_holds = (
        set(result.duplicate_review_hold_qb_rows)
        | set(result.amount_variance_review_hold_qb_rows)
        | set(result.ambiguous_duplicate_qb_rows)
        | set(result.fuzzy_match_review_hold_qb_rows)
    )
    matched: set[int] = set()
    for group in result.matches:
        matched.update(group.qb_rows)
    if not result.historical_clearances.empty:
        matched.update(_historical_row_indexes(result.historical_clearances, "QuickBooks Primary", "Primary Row Index"))
    if reference_hold_qb & (unmatched | duplicates | other_holds | matched):
        raise ValueError(
            "Reference-evidence review hold control failure: a held row also appears in another "
            "financial disposition population."
        )
    report = result.reference_hold_analysis
    if len(report) != len(reference_hold_qb) or (
        not report.empty and set(report["QuickBooks Row Index"].astype(int)) != reference_hold_qb
    ):
        raise ValueError(
            "Reference-evidence review hold audit control failure: report and held-row populations do not agree."
        )

    ledger = result.qb_dispositions
    if ledger is None or ledger.empty and len(result.qb_work):
        raise ValueError("QuickBooks disposition control failure: the final-disposition ledger is missing.")
    if ledger["QBO Row ID"].duplicated().any() or len(ledger) != len(result.qb_work):
        raise ValueError(
            "QuickBooks disposition control failure: every source row must have exactly one final disposition."
        )
    if set(ledger["Final Disposition"]) - set(FINAL_DISPOSITIONS):
        raise ValueError("QuickBooks disposition control failure: unknown final disposition.")
    id_by_index = result.qb_work[QB_ID].to_dict()
    expected = {
        DISPOSITION_MATCHED: {id_by_index[i] for i in matched},
        DISPOSITION_DUPLICATE_EXCLUDED: {id_by_index[i] for i in duplicates},
        DISPOSITION_REVIEW_HOLD: {id_by_index[i] for i in other_holds | reference_hold_qb},
        DISPOSITION_TRUE_UNMATCHED: {id_by_index[i] for i in unmatched},
    }
    for disposition, ids in expected.items():
        found = set(ledger.loc[ledger["Final Disposition"] == disposition, "QBO Row ID"])
        if found != ids:
            raise ValueError(
                f"QuickBooks disposition control failure: {disposition} rows in the ledger do not "
                "agree with the reconciliation populations."
            )
    # Nothing recognised as a duplicate, a possible duplicate, an amount conflict,
    # or an already-represented record may reach the automatic journal entry.
    journal = ledger.loc[ledger["In Proposed JE"] == "Yes"]
    if set(journal["Final Disposition"]) - {DISPOSITION_TRUE_UNMATCHED}:
        raise ValueError(
            "Proposed JE control failure: a row that is not TRUE_UNMATCHED feeds the journal entry."
        )
    if not journal.empty and journal["Reason Code"].str.startswith("REVIEW_HOLD").any():
        raise ValueError("Proposed JE control failure: a review-hold row feeds the journal entry.")


def validate_reconciliation(result: ReconciliationResult) -> None:
    if result.controls["Status"].ne("PASS").any():
        failures = result.controls.loc[result.controls["Status"] != "PASS", "Check"].tolist()
        raise ValueError(f"Reconciliation control failure: {', '.join(failures)}")
    duplicate_qb = set(result.duplicate_qb_rows)
    duplicate_inf = set(result.duplicate_inf_rows)
    review_hold_qb = set(result.duplicate_review_hold_qb_rows)
    review_hold_inf = set(result.duplicate_review_hold_inf_rows)
    variance_hold_qb = set(result.amount_variance_review_hold_qb_rows)
    variance_hold_inf = set(result.amount_variance_review_hold_inf_rows)
    ambiguous_qb = set(result.ambiguous_duplicate_qb_rows)
    fuzzy_hold_qb = set(result.fuzzy_match_review_hold_qb_rows)
    fuzzy_hold_inf = set(result.fuzzy_match_review_hold_inf_rows)
    reference_hold_qb = set(result.reference_hold_qb_rows)
    if duplicate_qb.intersection(result.suspected_qb_rows):
        raise ValueError(
            "Duplicate disposition control failure: a QuickBooks row was both "
            "excluded and retained for review."
        )
    if duplicate_inf.intersection(result.suspected_inf_rows):
        raise ValueError(
            "Duplicate disposition control failure: an Infinium row was both "
            "excluded and retained for review."
        )
    if not review_hold_qb.issubset(result.suspected_qb_rows):
        raise ValueError(
            "Duplicate Review Hold control failure: a held QuickBooks row was never "
            "a suspected duplicate candidate."
        )
    if not review_hold_inf.issubset(result.suspected_inf_rows):
        raise ValueError(
            "Duplicate Review Hold control failure: a held Infinium row was never "
            "a suspected duplicate candidate."
        )
    if review_hold_qb.intersection(result.unmatched_qb) or review_hold_inf.intersection(result.unmatched_inf):
        raise ValueError(
            "Duplicate Review Hold control failure: a held row also remained in the "
            "unresolved population used for the accrual/journal entry total."
        )
    if duplicate_qb.intersection(review_hold_qb) or duplicate_inf.intersection(review_hold_inf):
        raise ValueError(
            "Duplicate Review Hold control failure: a row was both auto-excluded and "
            "placed in Duplicate Review Hold."
        )
    if (
        variance_hold_qb.intersection(duplicate_qb | review_hold_qb | ambiguous_qb | set(result.unmatched_qb))
        or variance_hold_inf.intersection(duplicate_inf | review_hold_inf | set(result.unmatched_inf))
    ):
        raise ValueError(
            "Amount variance control failure: a variance-held row also appears in another "
            "financial disposition population."
        )
    if ambiguous_qb.intersection(duplicate_qb | review_hold_qb | set(result.unmatched_qb)):
        raise ValueError(
            "Ambiguous duplicate control failure: an ambiguous-duplicate row also appears "
            "in another financial disposition population."
        )
    ambiguous_report = result.ambiguous_duplicate_analysis
    if len(ambiguous_report) != len(ambiguous_qb):
        raise ValueError(
            "Ambiguous duplicate audit control failure: report and held-row counts do not agree."
        )
    if not ambiguous_report.empty:
        if ambiguous_report["QuickBooks Row Index"].duplicated().any():
            raise ValueError(
                "Ambiguous duplicate audit control failure: relationships are not one-per-row."
            )
        if set(ambiguous_report["QuickBooks Row Index"].astype(int)) != ambiguous_qb:
            raise ValueError(
                "Ambiguous duplicate audit control failure: QuickBooks row indexes disagree."
            )
        for ambiguous in ambiguous_report.to_dict("records"):
            if int(ambiguous["Candidate Count"]) < 2:
                raise ValueError(
                    "Ambiguous duplicate control failure: fewer than two candidates were held "
                    "as ambiguous."
                )
    po_reuse_report = result.po_reuse_errors
    if not po_reuse_report.empty:
        # A PO Re-use Error row with Infinium evidence is held; one with none
        # stays a true unmatched row. Either way it must sit in exactly one of
        # those two populations and never in another excluding one.
        po_reuse_qb_indexes = set(po_reuse_error_qb_index_map(po_reuse_report))
        if not po_reuse_qb_indexes.issubset(set(result.unmatched_qb) | reference_hold_qb):
            raise ValueError(
                "PO Re-use Error audit control failure: a flagged row is in neither the true "
                "unmatched population nor the review hold."
            )
        if po_reuse_qb_indexes.intersection(
            duplicate_qb | review_hold_qb | variance_hold_qb | ambiguous_qb
        ):
            raise ValueError(
                "PO Re-use Error audit control failure: a flagged row also appears in another "
                "financial disposition population."
            )
        for po_reuse in po_reuse_report.to_dict("records"):
            if int(po_reuse["QuickBooks Row Count"]) < 2:
                raise ValueError(
                    "PO Re-use Error control failure: fewer than two QuickBooks rows were "
                    "grouped under a reused PO."
                )
            expected_difference = (
                Decimal(str(po_reuse["QuickBooks Total"])) * 100
                - Decimal(str(po_reuse["Infinium Total"])) * 100
            )
            reported_difference = Decimal(str(po_reuse["Difference"])) * 100
            if reported_difference != expected_difference:
                raise ValueError("PO Re-use Error control failure: reported difference is incorrect.")
            if reported_difference == 0:
                raise ValueError(
                    "PO Re-use Error control failure: a zero-difference group was flagged as an error."
                )
    variance_report = result.amount_variance_analysis
    if len(variance_report) != len(variance_hold_qb) or len(variance_report) != len(variance_hold_inf):
        raise ValueError(
            "Amount variance audit control failure: report and held-row counts do not agree."
        )
    if not variance_report.empty:
        if (
            variance_report["Variance ID"].duplicated().any()
            or variance_report["QuickBooks Row Index"].duplicated().any()
            or variance_report["Infinium Row Index"].duplicated().any()
        ):
            raise ValueError(
                "Amount variance audit control failure: variance relationships are not one-to-one."
            )
        if set(variance_report["QuickBooks Row Index"].astype(int)) != variance_hold_qb:
            raise ValueError("Amount variance audit control failure: QuickBooks row indexes disagree.")
        if set(variance_report["Infinium Row Index"].astype(int)) != variance_hold_inf:
            raise ValueError("Amount variance audit control failure: Infinium row indexes disagree.")
        for variance in variance_report.to_dict("records"):
            qidx = int(variance["QuickBooks Row Index"])
            iidx = int(variance["Infinium Row Index"])
            q_cents = int(result.qb_work.at[qidx, AMOUNT_CENTS])
            i_cents = int(result.inf_work.at[iidx, AMOUNT_CENTS])
            if q_cents == i_cents:
                raise ValueError("Amount variance control failure: held amounts unexpectedly agree.")
            po_agrees = bool(
                result.qb_work.at[qidx, NORM_PO]
                and result.qb_work.at[qidx, NORM_PO] == result.inf_work.at[iidx, NORM_PO]
            )
            invoice_agrees = bool(
                result.qb_work.at[qidx, NORM_INV]
                and result.qb_work.at[qidx, NORM_INV] == result.inf_work.at[iidx, NORM_INV]
            )
            if not (po_agrees or invoice_agrees):
                raise ValueError("Amount variance control failure: no exact reference agrees.")
            reported_difference = Decimal(str(variance["Potential Difference"])) * 100
            if reported_difference != Decimal(q_cents - i_cents):
                raise ValueError("Amount variance control failure: reported difference is incorrect.")
    if (
        fuzzy_hold_qb.intersection(
            duplicate_qb | review_hold_qb | variance_hold_qb | ambiguous_qb | set(result.unmatched_qb)
        )
        or fuzzy_hold_inf.intersection(
            duplicate_inf | review_hold_inf | variance_hold_inf | set(result.unmatched_inf)
        )
    ):
        raise ValueError(
            "Fuzzy match review hold control failure: a fuzzy-held row also appears in "
            "another financial disposition population."
        )
    fuzzy_report = result.fuzzy_match_review_hold_analysis
    if fuzzy_report.empty != (not fuzzy_hold_qb and not fuzzy_hold_inf):
        raise ValueError(
            "Fuzzy match review hold audit control failure: report and held-row "
            "populations do not agree."
        )
    if not fuzzy_report.empty:
        reported_qb_rows: set[int] = set()
        reported_inf_rows: set[int] = set()
        for record in fuzzy_report.to_dict("records"):
            reported_qb_rows.update(
                int(idx) for idx in str(record["QuickBooks Row Indexes"]).split(";") if idx.strip()
            )
            reported_inf_rows.update(
                int(idx) for idx in str(record["Infinium Row Indexes"]).split(";") if idx.strip()
            )
        if reported_qb_rows != fuzzy_hold_qb:
            raise ValueError(
                "Fuzzy match review hold audit control failure: QuickBooks row indexes disagree."
            )
        if reported_inf_rows != fuzzy_hold_inf:
            raise ValueError(
                "Fuzzy match review hold audit control failure: Infinium row indexes disagree."
            )
    for group in result.matches:
        if review_hold_qb.intersection(group.qb_rows) or review_hold_inf.intersection(group.inf_rows):
            raise ValueError(
                "Duplicate Review Hold control failure: a held row was included in an "
                "accepted match."
            )
        if variance_hold_qb.intersection(group.qb_rows) or variance_hold_inf.intersection(group.inf_rows):
            raise ValueError(
                "Amount variance control failure: a variance-held row was included in an "
                "accepted match."
            )
        if ambiguous_qb.intersection(group.qb_rows):
            raise ValueError(
                "Ambiguous duplicate control failure: an ambiguous-duplicate row was included "
                "in an accepted match."
            )
        if fuzzy_hold_qb.intersection(group.qb_rows) or fuzzy_hold_inf.intersection(group.inf_rows):
            raise ValueError(
                "Fuzzy match review hold control failure: a fuzzy-held row was included in "
                "an accepted match."
            )
        if group.confidence == FUZZY_PO_CONFIDENCE:
            raise ValueError(
                "Fuzzy match review hold control failure: a fuzzy match was accepted "
                "instead of held for review."
            )
    for dataset, report, frame, id_column, excluded_rows in (
        ("QuickBooks", result.duplicate_analysis, result.qb_work, QB_ID, duplicate_qb | review_hold_qb),
        ("Infinium", result.infinium_duplicate_analysis, result.inf_work, INF_ID, duplicate_inf | review_hold_inf),
    ):
        if report.empty:
            if excluded_rows:
                raise ValueError(
                    f"Duplicate audit control failure: excluded {dataset} rows have no report."
                )
            continue
        required_columns = {
            "Duplicate Group ID", "Confirmed Copy Set ID", "Screening Stage",
            "Disposition", "Automatically Excluded", "Source Row ID",
            "Source Scope", "Potential Duplicate Reason",
            "Differing Confirmation Fields", "Duplicate Rule Version",
        }
        if not required_columns.issubset(report.columns):
            raise ValueError(f"Duplicate audit control failure: {dataset} report schema is incomplete.")
        if report["Duplicate Group ID"].isna().any() or report["Duplicate Rule Version"].ne(DUPLICATE_RULE_VERSION).any():
            raise ValueError(f"Duplicate audit control failure: {dataset} group IDs or rule versions are invalid.")
        primary_report = report.loc[report["Source Scope"] == "Primary"]
        reported_excluded_ids = set(
            primary_report.loc[
                flag_mask(primary_report["Automatically Excluded"]),
                "Source Row ID",
            ]
        )
        expected_excluded_ids = {frame.at[index, id_column] for index in excluded_rows}
        if reported_excluded_ids != expected_excluded_ids:
            raise ValueError(
                f"Duplicate audit control failure: excluded {dataset} rows do not agree with the report."
            )
        confirmed_primary = primary_report.loc[
            flag_mask(primary_report["Payload Confirmed"])
            & primary_report["Duplicate Basis"].ne("Primary/Historical overlap")
        ]
        for _, group in confirmed_primary.groupby("Confirmed Copy Set ID", sort=False):
            if group["Disposition"].eq("Retained canonical row").sum() != 1:
                raise ValueError(
                    f"Duplicate canonicalization control failure: {dataset} copy set does not retain exactly one canonical row."
                )
    for dataset, report, frame, id_column, excluded_rows, suspected_rows in (
        (
            "QuickBooks Historical", result.duplicate_analysis,
            result.qb_secondary_work, QB_ID,
            set(result.duplicate_qb_secondary_rows),
            set(result.suspected_qb_secondary_rows),
        ),
        (
            "Infinium Historical", result.infinium_duplicate_analysis,
            result.inf_secondary_work, INF_ID,
            set(result.duplicate_inf_secondary_rows),
            set(result.suspected_inf_secondary_rows),
        ),
    ):
        if frame is None:
            if excluded_rows or suspected_rows:
                raise ValueError(
                    f"Historical duplicate audit control failure: {dataset} has dispositions "
                    "without a working source frame."
                )
            continue
        historical_report = report.loc[
            report["Source Scope"].eq("Historical (Secondary)")
        ] if not report.empty else report
        reported_excluded_ids = set(
            historical_report.loc[
                flag_mask(historical_report["Automatically Excluded"]),
                "Source Row ID",
            ]
        ) if not historical_report.empty else set()
        expected_excluded_ids = {
            frame.at[index, id_column]
            for index in excluded_rows | suspected_rows
        }
        if reported_excluded_ids != expected_excluded_ids:
            raise ValueError(
                f"Historical duplicate audit control failure: withheld {dataset} rows "
                "do not agree with the duplicate report."
            )
    has_posting_blockers = result.metrics.get("Posting Blockers", "None") != "None"
    if has_posting_blockers != (result.metrics.get("Posting Status") == "REVIEW REQUIRED"):
        raise ValueError("Posting control failure: status does not agree with posting blockers.")
    expected_authorization = "DO NOT POST" if has_posting_blockers else "AUTHORIZED BY AUTOMATED CONTROLS"
    if result.metrics.get("Posting Authorization") != expected_authorization:
        raise ValueError("Posting control failure: authorization does not agree with posting blockers.")
    for group in result.matches:
        q_count = len(group.qb_rows)
        i_count = len(group.inf_rows)
        if duplicate_qb.intersection(group.qb_rows) or duplicate_inf.intersection(group.inf_rows):
            raise ValueError(
                "Duplicate exclusion control failure: a duplicate row was included in an "
                "accepted match."
            )
        if group.group_level:
            if not (
                (q_count == 1 and 2 <= i_count <= MAX_GROUP_SIZE)
                or (i_count == 1 and 2 <= q_count <= MAX_GROUP_SIZE)
            ):
                raise ValueError(
                    "Grouped matching control failure: only bounded one-to-many "
                    "or many-to-one relationships are permitted."
                )
        elif q_count != 1 or i_count != 1:
            raise ValueError(
                "One-to-one matching control failure: a non-group match has "
                "unexpected cardinality."
            )
        q_total = _amount_total(result.qb_work, group.qb_rows)
        i_total = _amount_total(result.inf_work, group.inf_rows)
        if q_total != i_total:
            raise ValueError(
                "Matching control failure: accepted relationship totals do not agree."
            )
    if duplicate_qb.intersection(result.unmatched_qb) or duplicate_inf.intersection(result.unmatched_inf):
        raise ValueError(
            "Duplicate exclusion control failure: a duplicate row remained in the "
            "unresolved population used for the accrual/journal entry total."
        )
    if not result.historical_clearances.empty:
        clearance_differences = result.historical_clearances.groupby(
            "Clearance ID", sort=False
        )["Amount Difference"].sum().round(2)
        if clearance_differences.ne(0).any():
            raise ValueError(
                "Historical clearance control failure: aggregate signed amounts "
                "must agree exactly."
            )
        primary_rows = result.historical_clearances.loc[
            result.historical_clearances["Primary Row Index"].notna(),
            ["Primary Dataset", "Primary Row Index"],
        ]
        if primary_rows.duplicated().any():
            raise ValueError("Historical clearance control failure: a primary row was cleared more than once.")
        secondary_rows = result.historical_clearances.loc[
            result.historical_clearances["Secondary Row Index"].notna(),
            ["Secondary Dataset", "Secondary Row Index"],
        ]
        if secondary_rows.duplicated().any():
            raise ValueError("Historical clearance control failure: a secondary row was used more than once.")
        duplicate_qb_secondary = set(result.duplicate_qb_secondary_rows)
        duplicate_inf_secondary = set(result.duplicate_inf_secondary_rows)
        review_qb_secondary = set(result.suspected_qb_secondary_rows)
        review_inf_secondary = set(result.suspected_inf_secondary_rows)
        used_qb_secondary = {
            int(value) for value in result.historical_clearances.loc[
                result.historical_clearances["Secondary Dataset"] == "QuickBooks Secondary (Historical)",
                "Secondary Row Index",
            ].dropna()
        }
        used_inf_secondary = {
            int(value) for value in result.historical_clearances.loc[
                result.historical_clearances["Secondary Dataset"] == "Infinium Secondary (Historical)",
                "Secondary Row Index",
            ].dropna()
        }
        if duplicate_qb_secondary.intersection(used_qb_secondary) or duplicate_inf_secondary.intersection(used_inf_secondary):
            raise ValueError(
                "Duplicate exclusion control failure: a duplicated historical row was used to "
                "clear a primary exception."
            )
        if review_qb_secondary.intersection(used_qb_secondary) or review_inf_secondary.intersection(used_inf_secondary):
            raise ValueError(
                "Historical duplicate review control failure: an unresolved historical "
                "candidate was used to clear a primary exception."
            )
        for _, clearance in result.historical_clearances.groupby(
            "Clearance ID", sort=False
        ):
            primary_count = int(clearance["Primary Row Index"].notna().sum())
            secondary_count = int(clearance["Secondary Row Index"].notna().sum())
            group_level = bool(clearance["Group-Level Match"].iloc[0])
            if group_level and not (
                (primary_count == 1 and 2 <= secondary_count <= MAX_GROUP_SIZE)
                or (secondary_count == 1 and 2 <= primary_count <= MAX_GROUP_SIZE)
            ):
                raise ValueError(
                    "Historical grouped clearance has invalid cardinality."
                )
            if not group_level and (primary_count != 1 or secondary_count != 1):
                raise ValueError(
                    "Historical one-to-one clearance has invalid cardinality."
                )
    exception_sections = {
        REFERENCE_HOLD_SECTION,
        "02 Unmatched QuickBooks",
        "03 Unmatched Infinium",
        "04 Duplicate QuickBooks",
        "05 Duplicate Infinium",
        "06 Duplicate Review Hold QuickBooks",
        "07 Duplicate Review Hold Infinium",
        "08 Reference-Matched Amount Variance Review Hold",
        "09 Fuzzy Match Review Hold",
        "10 Ambiguous Duplicate QuickBooks",
    }
    duplicate_sections = {"04 Duplicate QuickBooks", "05 Duplicate Infinium"}
    for row in result.paired_rows:
        section = row.get("Section")
        if section not in exception_sections:
            continue
        if not row.get("Exception Cause") or not row.get("Financial Treatment"):
            raise ValueError(
                "Exception-cause audit control failure: an exception row lacks its "
                "standardized cause or financial treatment."
            )
        if section in duplicate_sections and not all(
            row.get(field)
            for field in (
                "Duplicate Values", "Duplicate Group ID",
                "Confirmed Copy Set ID", "Canonical Source Row ID",
            )
        ):
            raise ValueError(
                "Duplicate-value audit control failure: a confirmed duplicate row lacks "
                "the values or canonical linkage required in Reconciliation Detail."
            )
        if section in {
            "06 Duplicate Review Hold QuickBooks",
            "07 Duplicate Review Hold Infinium",
        } and not row.get("Potential Duplicate Reason"):
            raise ValueError(
                "Weak-basis duplicate audit control failure: a review-held row lacks "
                "the specific evidence that caused its classification."
            )
    qb_occurrences = Counter(
        row["QB Index"]
        for row in result.paired_rows
        if row["QB Index"] is not None and row.get("QB Record Scope") == "Primary"
    )
    inf_occurrences = Counter(
        row["Infinium Index"]
        for row in result.paired_rows
        if row["Infinium Index"] is not None
        and row.get("Infinium Record Scope") == "Primary"
    )
    expected_qb = set(result.qb_work.index)
    expected_inf = set(result.inf_work.index)
    if set(qb_occurrences) != expected_qb or any(count != 1 for count in qb_occurrences.values()):
        raise ValueError("QuickBooks rows were dropped or duplicated while building the reconciled output.")
    if set(inf_occurrences) != expected_inf or any(count != 1 for count in inf_occurrences.values()):
        raise ValueError("Infinium rows were dropped or duplicated while building the reconciled output.")
    # Last, so every existing control keeps precedence (and its own message)
    # over the disposition-ledger and match-reference controls.
    _validate_reference_holds_and_dispositions(result, reference_hold_qb)
    validate_match_references(result)
