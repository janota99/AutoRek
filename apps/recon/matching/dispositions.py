"""Final disposition of every QuickBooks row, fuzzy-match review holds, and historical-row clearances."""

from __future__ import annotations

import re
from itertools import zip_longest
from typing import Any, Optional

import pandas as pd

from ..duplicates import AMOUNT_CENTS, NORM_INV, NORM_PO, SOURCE_POS
from ..fuzzy_po_matching import PO_TOKENS, TYPO_PO_METHOD
from .core import (
    _amount_total,
    _split_reference_ids,
    cents_or_zero,
    cents_to_float,
    INF_ID,
    MatchGroup,
    QB_ID,
    valid_cents,
)
from .labels import (
    DISPOSITION_DUPLICATE_EXCLUDED,
    DISPOSITION_MATCHED,
    DISPOSITION_REVIEW_HOLD,
    DISPOSITION_TRUE_UNMATCHED,
    HOLD_AMOUNT_VARIANCE,
    HOLD_POTENTIAL_DUPLICATE,
    MATCH_REF_COLUMN,
    QB_DISPOSITION_COLUMNS,
    REFERENCE_HOLD_SECTION,
    REFERENCED_MATCH_REF_COLUMN,
)
from .engine import perform_matching
from .references import describe_match_references


_MATCH_REASONS = {
    "PO + Invoice + Amount": ("MATCH_EXACT_PO_INVOICE_AMOUNT", "Exact PO + Invoice + Amount"),
    "PO + Amount": ("MATCH_EXACT_PO_AMOUNT", "Exact PO + Amount"),
    "Invoice + Amount": ("MATCH_EXACT_INVOICE_AMOUNT", "Exact Invoice + Amount"),
    "PO + Invoice + Aggregate Amount (Grouped)": (
        "MATCH_AGGREGATE_PO_INVOICE_AMOUNT", "Aggregate PO + Invoice + Exact Amount",
    ),
    "PO + Aggregate Amount (Grouped)": ("MATCH_AGGREGATE_PO_AMOUNT", "Aggregate PO + Exact Amount"),
    "Invoice + Aggregate Amount (Grouped)": (
        "MATCH_AGGREGATE_INVOICE_AMOUNT", "Aggregate Invoice + Exact Amount",
    ),
    TYPO_PO_METHOD: ("MATCH_CONTROLLED_PO_TYPO", TYPO_PO_METHOD),
}


_SECTION_HOLDS = {
    "06 Duplicate Review Hold QuickBooks": (
        "REVIEW_HOLD_POTENTIAL_DUPLICATE", "Review Hold — Potential Duplicate (Insufficient Evidence)",
    ),
    "08 Reference-Matched Amount Variance Review Hold": (
        HOLD_AMOUNT_VARIANCE, "Review Hold — Amount Variance",
    ),
    "09 Fuzzy Match Review Hold": (
        "REVIEW_HOLD_FUZZY_MATCH", "Review Hold — Fuzzy PO Whole-Word Containment + Exact Amount",
    ),
    "10 Ambiguous Duplicate QuickBooks": (
        "REVIEW_HOLD_AMBIGUOUS_CANDIDATES", "Review Hold — Ambiguous Candidates (Multiple Infinium Records)",
    ),
}


def build_qb_dispositions(
    paired_rows: list[dict[str, Any]],
    qb: pd.DataFrame,
    po_reuse_qb_indexes: set[int],
    register: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """One row per QuickBooks source row -- every one, in source order -- with
    exactly one of four final dispositions and the precise reason for it:

        MATCHED | EXACT_QBO_DUPLICATE_EXCLUDED | REVIEW_HOLD | TRUE_UNMATCHED

    Built from the same paired rows every workbook reads, so this ledger and
    the reconciliation can never disagree. Only TRUE_UNMATCHED rows feed the
    proposed journal entry."""
    matched_sections = {"01 Matched", "01 Matched - Historical Clearance"}
    rows = [
        row for row in paired_rows
        if row.get("QB Index") is not None and row.get("QB Record Scope") == "Primary"
    ]
    rows.sort(key=lambda row: qb.at[row["QB Index"], SOURCE_POS])
    records: list[dict[str, Any]] = []
    review_number = 0
    for row in rows:
        qidx = int(row["QB Index"])
        section = str(row.get("Section"))
        result_text = str(row.get("Match Result") or "")
        match_ref = str(row.get(MATCH_REF_COLUMN) or "")
        related_ref = str(row.get(REFERENCED_MATCH_REF_COLUMN) or "")
        duplicate_group = ""
        canonical = ""
        if section in matched_sections:
            disposition = DISPOSITION_MATCHED
            method = result_text.split("|")[-1].strip().replace(" [group-level; no line allocation]", "")
            code, reason = _MATCH_REASONS.get(method, ("MATCH_" + re.sub(r"[^A-Z0-9]+", "_", method.upper()).strip("_"), method))
            if section == "01 Matched - Historical Clearance":
                code, reason = "PRIOR_PERIOD_" + code, f"Prior-Period Clearance — {reason}"
        elif section == "04 Duplicate QuickBooks":
            disposition = DISPOSITION_DUPLICATE_EXCLUDED
            code = "EXACT_QBO_DUPLICATE_EXCESS_COPY"
            reason = "Confirmed Exact QBO Duplicate — Excess Copy Excluded"
            duplicate_group = str(row.get("Duplicate Group ID") or "")
            canonical = str(row.get("Canonical Source Row ID") or "")
            basis = str(row.get("Confirmation Basis") or "")
            detail_bits = [
                f"canonical row {canonical} retained" if canonical else "",
                f"confirmed by {basis.lower()}" if basis else "",
            ]
            detail_text = "; ".join(bit for bit in detail_bits if bit)
            if detail_text:
                reason += f" ({detail_text})"
        elif section == REFERENCE_HOLD_SECTION:
            disposition = DISPOSITION_REVIEW_HOLD
            code = str(row.get("Reason Code") or "REVIEW_HOLD_REFERENCE_EVIDENCE")
            reason = result_text
        elif section in _SECTION_HOLDS:
            disposition = DISPOSITION_REVIEW_HOLD
            code, reason = _SECTION_HOLDS[section]
            if section == "06 Duplicate Review Hold QuickBooks":
                duplicate_group = str(row.get("Duplicate Group ID") or "")
                canonical = str(row.get("Canonical Source Row ID") or "")
                code = HOLD_POTENTIAL_DUPLICATE
                if related_ref:
                    reason = (
                        "Review Hold — Potential Duplicate of "
                        f"{describe_match_references(_split_reference_ids(related_ref))}"
                        + (f" (matched sibling {canonical})" if canonical else "")
                    )
                elif canonical:
                    reason = f"Review Hold — Potential Duplicate of {canonical} (suspected canonical row)"
                else:
                    reason = "Review Hold — Potential Duplicate (Insufficient Evidence)"
        elif section == "02 Unmatched QuickBooks":
            disposition = DISPOSITION_TRUE_UNMATCHED
            if qidx in po_reuse_qb_indexes:
                code = "TRUE_UNMATCHED_PO_REUSE"
                reason = "True Unmatched — Repeated PO with no Infinium support"
            else:
                code = "TRUE_UNMATCHED_NO_INFINIUM_CANDIDATE"
                reason = "True Unmatched — No Remaining Infinium Candidate"
        else:
            raise ValueError(f"QuickBooks disposition control failure: unclassified section {section!r}.")
        review_id = ""
        if disposition == DISPOSITION_REVIEW_HOLD:
            review_number += 1
            review_id = f"REV-{review_number:03d}" if review_number < 1000 else f"REV-{review_number}"
        cents = qb.at[qidx, AMOUNT_CENTS]
        records.append({
            "QBO Row ID": qb.at[qidx, QB_ID],
            "Normalized PO": qb.at[qidx, NORM_PO],
            "Normalized Invoice": qb.at[qidx, NORM_INV],
            "Amount": cents_to_float(cents) if valid_cents(cents) else None,
            "Final Disposition": disposition,
            "Reason Code": code,
            "Final Reason": reason,
            "Match Ref.": match_ref,
            "Aggregate Group ID": match_ref if match_ref.startswith("G-") else "",
            "Duplicate Group ID": duplicate_group,
            "Canonical QBO Row ID": canonical,
            "Review ID": review_id,
            "Related Match Ref.": related_ref,
            "Related Infinium Row IDs": (
                str(row.get("Related Source Row IDs") or "")
                if disposition == DISPOSITION_REVIEW_HOLD and section != "06 Duplicate Review Hold QuickBooks"
                else ""
            ),
            "In Proposed JE": "Yes" if disposition == DISPOSITION_TRUE_UNMATCHED else "No",
        })
    return pd.DataFrame(records, columns=QB_DISPOSITION_COLUMNS)


def _final_disposition_metrics(dispositions: pd.DataFrame) -> dict[str, Any]:
    """Row counts and signed dollars per final disposition, straight from the
    disposition ledger -- and the proposed JE, which is by definition the sum
    of the TRUE_UNMATCHED rows and nothing else."""
    labels = {
        DISPOSITION_MATCHED: "Matched",
        DISPOSITION_DUPLICATE_EXCLUDED: "Duplicate Excluded",
        DISPOSITION_REVIEW_HOLD: "Review Hold",
        DISPOSITION_TRUE_UNMATCHED: "True Unmatched",
    }
    metrics: dict[str, Any] = {}
    for disposition, label in labels.items():
        subset = dispositions.loc[dispositions["Final Disposition"] == disposition]
        metrics[f"Final Disposition - {label} Rows"] = len(subset)
        metrics[f"Final Disposition - {label} Amount"] = round(float(subset["Amount"].fillna(0).sum()), 2)
    metrics["Proposed JE Amount"] = metrics["Final Disposition - True Unmatched Amount"]
    return metrics


FUZZY_MATCH_REVIEW_COLUMNS = [
    "Fuzzy Match ID", "Classification", "Confidence", "Match Basis",
    "QuickBooks Row IDs", "QuickBooks Row Indexes", "Infinium Row IDs",
    "Infinium Row Indexes", "QuickBooks Row Count", "Infinium Row Count",
    "Matched Text Evidence", "QuickBooks Amount", "Infinium Amount",
    "Accrual Treatment", "Posting Disposition", "Explanation",
    "Manual Decision", "Reviewed By", "Review Timestamp", "Review Rationale",
]


FUZZY_MATCH_CLASSIFICATION_SINGLE = "Fuzzy Match - Possible Text Variant of PO or Invoice"


FUZZY_MATCH_CLASSIFICATION_GROUPED = "Fuzzy Match - Multiple Line Items Netting to One Total"


FUZZY_MATCH_CONFIDENCE = "Review"


def build_fuzzy_match_review_holds(
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    fuzzy_groups: list[MatchGroup],
) -> pd.DataFrame:
    """Report every fuzzy PO/text match held for a documented human decision.

    A fuzzy match is a text-similarity guess, not a certain relationship --
    unlike an exact match it is never posted automatically. This produces the
    plain-English, auditor-facing report; the caller is responsible for
    removing these rows from ``matches`` and treating them as held rather
    than accepted (see ``build_reconciliation``).
    """
    records: list[dict[str, Any]] = []
    for number, group in enumerate(fuzzy_groups, 1):
        q_rows = sorted(group.qb_rows, key=lambda idx: qb.at[idx, SOURCE_POS])
        i_rows = sorted(group.inf_rows, key=lambda idx: inf.at[idx, SOURCE_POS])
        grouped = len(q_rows) > 1 or len(i_rows) > 1
        classification = (
            FUZZY_MATCH_CLASSIFICATION_GROUPED if grouped else FUZZY_MATCH_CLASSIFICATION_SINGLE
        )
        shared_tokens: set[str] = set()
        for qidx in q_rows:
            for iidx in i_rows:
                shared_tokens |= qb.at[qidx, PO_TOKENS] & inf.at[iidx, PO_TOKENS]
        q_total = _amount_total(qb, q_rows)
        i_total = _amount_total(inf, i_rows)
        records.append({
            "Fuzzy Match ID": f"FUZZY-{number:06d}",
            "Classification": classification,
            "Confidence": FUZZY_MATCH_CONFIDENCE,
            "Match Basis": (
                f"Shared PO/text tokens ({', '.join(sorted(shared_tokens)) or 'n/a'}) "
                "and exact aggregate amount tie-out"
            ),
            "QuickBooks Row IDs": "; ".join(str(qb.at[idx, QB_ID]) for idx in q_rows),
            "QuickBooks Row Indexes": "; ".join(str(idx) for idx in q_rows),
            "Infinium Row IDs": "; ".join(str(inf.at[idx, INF_ID]) for idx in i_rows),
            "Infinium Row Indexes": "; ".join(str(idx) for idx in i_rows),
            "QuickBooks Row Count": len(q_rows),
            "Infinium Row Count": len(i_rows),
            "Matched Text Evidence": ", ".join(sorted(shared_tokens)) or "n/a",
            "QuickBooks Amount": cents_to_float(q_total),
            "Infinium Amount": cents_to_float(i_total),
            "Accrual Treatment": (
                "Excluded from automatic JE; a text-similarity match is never posted "
                "without documented review"
            ),
            "Posting Disposition": "REVIEW REQUIRED - DO NOT POST",
            "Explanation": (
                f"{group.explanation} Neither amount is automatically posted pending "
                "documented review."
            ),
            "Manual Decision": None,
            "Reviewed By": None,
            "Review Timestamp": None,
            "Review Rationale": None,
        })
    return pd.DataFrame(records, columns=FUZZY_MATCH_REVIEW_COLUMNS)


HISTORICAL_CLEARANCE_COLUMNS = [
    "Clearance ID",
    "Group Sequence",
    "Primary Row Count",
    "Secondary Row Count",
    "Group-Level Match",
    "Primary Dataset",
    "Primary Row ID",
    "Primary Row Index",
    "Secondary Dataset",
    "Secondary Row ID",
    "Secondary Row Index",
    "Match Method",
    "Confidence",
    "Normalized PO",
    "Normalized Invoice",
    "Primary Amount",
    "Secondary Amount",
    "Amount Difference",
    "Disposition",
]


def build_historical_clearances(
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    unmatched_qb: list[int],
    unmatched_inf: list[int],
    qb_secondary: Optional[pd.DataFrame],
    inf_secondary: Optional[pd.DataFrame],
) -> tuple[pd.DataFrame, list[int], list[int]]:
    """Clear opposing-primary exceptions with optional historical sources.

    Historical rows never become exceptions themselves. Accepted one-to-one
    and controlled grouped matches are retained as evidence; all unused
    historical rows are discarded from the reconciliation population.

    ``qb_secondary``/``inf_secondary`` must already be duplicate-screened by
    the caller (see ``duplicates.screen_duplicates``) -- a duplicated
    historical row is just as capable of improperly clearing a real primary
    exception as a duplicated primary row is of misstating the accrual, so
    neither may reach this function.
    """
    records: list[dict[str, Any]] = []
    cleared_qb: set[int] = set()
    cleared_inf: set[int] = set()
    clearance_number = 0

    def append_clearance(
        group: MatchGroup,
        primary_frame: pd.DataFrame,
        secondary_frame: pd.DataFrame,
        primary_indexes: list[int],
        secondary_indexes: list[int],
        primary_dataset: str,
        secondary_dataset: str,
        primary_id_column: str,
        secondary_id_column: str,
        method_prefix: str,
        disposition: str,
    ) -> None:
        nonlocal clearance_number
        clearance_number += 1
        clearance_id = f"H-{clearance_number:06d}"
        ordered_primary = sorted(
            (int(idx) for idx in primary_indexes),
            key=lambda idx: primary_frame.at[idx, SOURCE_POS],
        )
        ordered_secondary = sorted(
            (int(idx) for idx in secondary_indexes),
            key=lambda idx: secondary_frame.at[idx, SOURCE_POS],
        )
        primary_total = _amount_total(primary_frame, ordered_primary)
        secondary_total = _amount_total(secondary_frame, ordered_secondary)
        for sequence, (pidx, sidx) in enumerate(
            zip_longest(ordered_primary, ordered_secondary), 1
        ):
            primary_amount = (
                cents_or_zero(primary_frame.at[pidx, AMOUNT_CENTS])
                if pidx is not None else 0
            )
            secondary_amount = (
                cents_or_zero(secondary_frame.at[sidx, AMOUNT_CENTS])
                if sidx is not None else 0
            )
            reference_frame = primary_frame if pidx is not None else secondary_frame
            reference_index = pidx if pidx is not None else sidx
            records.append(
                {
                    "Clearance ID": clearance_id,
                    "Group Sequence": sequence,
                    "Primary Row Count": len(ordered_primary),
                    "Secondary Row Count": len(ordered_secondary),
                    "Group-Level Match": group.group_level,
                    "Primary Dataset": primary_dataset,
                    "Primary Row ID": (
                        primary_frame.at[pidx, primary_id_column]
                        if pidx is not None else ""
                    ),
                    "Primary Row Index": int(pidx) if pidx is not None else None,
                    "Secondary Dataset": secondary_dataset,
                    "Secondary Row ID": (
                        secondary_frame.at[sidx, secondary_id_column]
                        if sidx is not None else ""
                    ),
                    "Secondary Row Index": int(sidx) if sidx is not None else None,
                    "Match Method": f"{method_prefix} | {group.method}",
                    "Confidence": group.confidence,
                    "Normalized PO": reference_frame.at[reference_index, NORM_PO],
                    "Normalized Invoice": reference_frame.at[reference_index, NORM_INV],
                    "Primary Amount": cents_to_float(primary_amount),
                    "Secondary Amount": cents_to_float(secondary_amount),
                    "Amount Difference": cents_to_float(primary_amount - secondary_amount),
                    "Disposition": disposition,
                }
            )
        if primary_total != secondary_total:
            raise ValueError(
                "Historical clearance construction failure: aggregate amounts differ."
            )

    if inf_secondary is not None and len(inf_secondary) and unmatched_qb:
        secondary_matches, _, _, _ = perform_matching(
            qb.loc[unmatched_qb].copy(), inf_secondary, enable_fuzzy=False
        )
        for group in secondary_matches:
            cleared_qb.update(int(idx) for idx in group.qb_rows)
            append_clearance(
                group,
                qb,
                inf_secondary,
                group.qb_rows,
                group.inf_rows,
                "QuickBooks Primary",
                "Infinium Secondary (Historical)",
                QB_ID,
                INF_ID,
                "QuickBooks primary ↔ Infinium prior-period match",
                (
                    "QuickBooks primary item(s) cleared by the displayed Infinium "
                    "prior-period item(s)"
                ),
            )

    if qb_secondary is not None and len(qb_secondary) and unmatched_inf:
        secondary_matches, _, _, _ = perform_matching(
            qb_secondary, inf.loc[unmatched_inf].copy(), enable_fuzzy=False
        )
        for group in secondary_matches:
            cleared_inf.update(int(idx) for idx in group.inf_rows)
            append_clearance(
                group,
                inf,
                qb_secondary,
                group.inf_rows,
                group.qb_rows,
                "Infinium Primary",
                "QuickBooks Secondary (Historical)",
                INF_ID,
                QB_ID,
                "QuickBooks prior-period match ↔ Infinium primary",
                (
                    "Infinium primary item(s) cleared by the displayed QuickBooks "
                    "prior-period item(s)"
                ),
            )

    clearances = pd.DataFrame(records, columns=HISTORICAL_CLEARANCE_COLUMNS)
    remaining_qb = sorted(set(unmatched_qb).difference(cleared_qb))
    remaining_inf = sorted(set(unmatched_inf).difference(cleared_inf))
    return clearances, remaining_qb, remaining_inf
