"""The side-by-side paired-row table and the per-match assessments."""

from __future__ import annotations

from collections import defaultdict
from itertools import zip_longest
from typing import Any, Optional

import pandas as pd

from ..duplicates import AMOUNT_CENTS, NORM_INV, NORM_PO, SOURCE_POS
from .core import (
    _amount_total,
    _optional_index,
    cents_to_float,
    INF_ID,
    MatchGroup,
    QB_ID,
    valid_cents,
)
from .labels import (
    HOLD_AMOUNT_VARIANCE,
    HOLD_HISTORICAL_CLEARANCE,
    HOLD_PO_REUSE,
    REFERENCE_HOLD_SECTION,
)
from .exceptions import po_reuse_error_qb_index_map
from .references import apply_referenced_match_references
from .dispositions import (
    FUZZY_MATCH_CLASSIFICATION_GROUPED,
    FUZZY_MATCH_CLASSIFICATION_SINGLE,
    FUZZY_MATCH_CONFIDENCE,
)


def build_paired_rows(
    matches: list[MatchGroup],
    historical_clearances: pd.DataFrame,
    unmatched_qb: list[int],
    unmatched_inf: list[int],
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    candidates: pd.DataFrame,
    duplicate_qb_rows: list[int],
    duplicate_inf_rows: list[int],
    duplicate_review_hold_qb_rows: list[int],
    duplicate_review_hold_inf_rows: list[int],
    amount_variance_analysis: pd.DataFrame,
    ambiguous_duplicate_analysis: pd.DataFrame,
    po_reuse_errors: pd.DataFrame,
    fuzzy_review_hold_groups: list[MatchGroup],
    qb_duplicate_report: Optional[pd.DataFrame] = None,
    inf_duplicate_report: Optional[pd.DataFrame] = None,
    match_register: Optional[pd.DataFrame] = None,
    reference_hold_analysis: Optional[pd.DataFrame] = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidate_reason = (
        candidates.set_index("QuickBooks Row ID")["Disposition"].to_dict()
        if not candidates.empty else {}
    )
    candidate_cause = (
        candidates.set_index("QuickBooks Row ID")["Exception Cause"].to_dict()
        if not candidates.empty else {}
    )
    for group in matches:
        ordered_q = sorted(group.qb_rows, key=lambda idx: qb.at[idx, SOURCE_POS])
        ordered_i = sorted(group.inf_rows, key=lambda idx: inf.at[idx, SOURCE_POS])
        for sequence, (qidx, iidx) in enumerate(zip_longest(ordered_q, ordered_i), 1):
            result_label = group.method
            if group.group_level:
                result_label += " [group-level; no line allocation]"
            rows.append(
                {
                    "Section": "01 Matched",
                    "Match ID": group.match_id,
                    "Match Ref.": group.match_ref,
                    "Match Result": result_label,
                    "QB Index": qidx,
                    "Infinium Index": iidx,
                    "QB Record Scope": "Primary",
                    "Infinium Record Scope": "Primary",
                    "Group Sequence": sequence,
                    "Confidence": group.confidence,
                    "Explanation": group.explanation,
                }
            )
    if not historical_clearances.empty:
        for clearance in historical_clearances.to_dict("records"):
            primary_is_qb = clearance["Primary Dataset"] == "QuickBooks Primary"
            primary_index = _optional_index(clearance["Primary Row Index"])
            secondary_index = _optional_index(clearance["Secondary Row Index"])
            qb_index = primary_index if primary_is_qb else secondary_index
            inf_index = secondary_index if primary_is_qb else primary_index
            rows.append(
                {
                    "Section": "01 Matched - Historical Clearance",
                    "Match ID": clearance["Clearance ID"],
                    "Match Ref.": clearance.get("Match Ref.", ""),
                    "Match Result": clearance["Match Method"],
                    "QB Index": qb_index,
                    "Infinium Index": inf_index,
                    "QB Record Scope": (
                        ("Primary" if primary_is_qb else "Historical")
                        if qb_index is not None else None
                    ),
                    "Infinium Record Scope": (
                        ("Historical" if primary_is_qb else "Primary")
                        if inf_index is not None else None
                    ),
                    "Group Sequence": clearance.get("Group Sequence", 1),
                    "Confidence": clearance["Confidence"],
                    "Explanation": clearance["Disposition"],
                }
            )
    for qidx in unmatched_qb:
        qb_id = qb.at[qidx, QB_ID]
        rows.append(
            {
                "Section": "02 Unmatched QuickBooks",
                "Match ID": "",
                "Match Result": candidate_reason.get(qb_id, "Unmatched QuickBooks"),
                "QB Index": qidx,
                "Infinium Index": None,
                "QB Record Scope": "Primary",
                "Infinium Record Scope": None,
                "Group Sequence": None,
                "Confidence": "Review",
                "Explanation": "No unique automatic Infinium match was established.",
            }
        )
    for iidx in unmatched_inf:
        rows.append(
            {
                "Section": "03 Unmatched Infinium",
                "Match ID": "",
                "Match Result": "Unmatched Infinium",
                "QB Index": None,
                "Infinium Index": iidx,
                "QB Record Scope": None,
                "Infinium Record Scope": "Primary",
                "Group Sequence": None,
                "Confidence": "Review",
                "Explanation": "No unique automatic QuickBooks match was established.",
            }
        )
    for qidx in duplicate_qb_rows:
        rows.append(
            {
                "Section": "04 Duplicate QuickBooks",
                "Match ID": "",
                "Match Result": "Excluded excess QuickBooks copy",
                "QB Index": qidx,
                "Infinium Index": None,
                "QB Record Scope": "Primary",
                "Infinium Record Scope": None,
                "Group Sequence": None,
                "Confidence": "Duplicate",
                "Explanation": (
                    "An earlier canonical QuickBooks row with the same normalized PO, "
                    "invoice, and signed cents was retained. Only this excess copy was "
                    "excluded from matching and the proposed journal entry."
                ),
            }
        )
    for iidx in duplicate_inf_rows:
        rows.append(
            {
                "Section": "05 Duplicate Infinium",
                "Match ID": "",
                "Match Result": "Excluded excess Infinium copy",
                "QB Index": None,
                "Infinium Index": iidx,
                "QB Record Scope": None,
                "Infinium Record Scope": "Primary",
                "Group Sequence": None,
                "Confidence": "Duplicate",
                "Explanation": (
                    "An earlier canonical Infinium row with the same normalized PO, "
                    "invoice, and signed cents was retained. Only this excess copy was "
                    "excluded from matching and is reported separately."
                ),
            }
        )
    for qidx in duplicate_review_hold_qb_rows:
        rows.append(
            {
                "Section": "06 Duplicate Review Hold QuickBooks",
                "Match ID": "",
                "Match Result": "Review Hold — Potential Duplicate (not confirmed as a copy)",
                "QB Index": qidx,
                "Infinium Index": None,
                "QB Record Scope": "Primary",
                "Infinium Record Scope": None,
                "Group Sequence": None,
                "Confidence": "Hold",
                "Explanation": (
                    "This row shares its duplicate key (PO, invoice, and signed amount -- or only "
                    "a PO or only an invoice) with another QuickBooks row, but nothing establishes "
                    "that it is a copy of the same underlying transaction, so it was never "
                    "discarded: it stayed active and eligible to match normally. It did not "
                    "match, so it is excluded from the proposed JE support total and held here "
                    "pending a documented human disposition. It may be a legitimate repeated sale."
                ),
            }
        )
    for iidx in duplicate_review_hold_inf_rows:
        rows.append(
            {
                "Section": "07 Duplicate Review Hold Infinium",
                "Match ID": "",
                "Match Result": "Weak-basis duplicate candidate - unresolved",
                "QB Index": None,
                "Infinium Index": iidx,
                "QB Record Scope": None,
                "Infinium Record Scope": "Primary",
                "Group Sequence": None,
                "Confidence": "Hold",
                "Explanation": (
                    "This row shares only a PO or only an invoice (never both) with another "
                    "Infinium row at the same signed amount. It remained active and eligible "
                    "to match normally, did not match anything, and is held here for review. "
                    "Infinium items never feed the QuickBooks accrual regardless."
                ),
            }
        )
    if not amount_variance_analysis.empty:
        for variance in amount_variance_analysis.to_dict("records"):
            rows.append(
                {
                    "Section": "08 Reference-Matched Amount Variance Review Hold",
                    "Match ID": variance["Variance ID"],
                    "Match Result": variance["Classification"],
                    "QB Index": int(variance["QuickBooks Row Index"]),
                    "Infinium Index": int(variance["Infinium Row Index"]),
                    "QB Record Scope": "Primary",
                    "Infinium Record Scope": "Primary",
                    "Group Sequence": 1,
                    "Confidence": variance["Confidence"],
                    "Explanation": (
                        f"{variance['Reference Evidence']}. QuickBooks "
                        f"{variance['QuickBooks Amount']}; Infinium "
                        f"{variance['Infinium Amount']}; potential difference "
                        f"{variance['Potential Difference']}. Neither amount is "
                        "automatically posted pending documented review."
                    ),
                }
            )
    if not ambiguous_duplicate_analysis.empty:
        for ambiguous in ambiguous_duplicate_analysis.to_dict("records"):
            rows.append(
                {
                    "Section": "10 Ambiguous Duplicate QuickBooks",
                    "Match ID": ambiguous["Ambiguous ID"],
                    "Match Result": ambiguous["Classification"],
                    "QB Index": int(ambiguous["QuickBooks Row Index"]),
                    "Infinium Index": None,
                    "QB Record Scope": "Primary",
                    "Infinium Record Scope": None,
                    "Group Sequence": 1,
                    "Confidence": ambiguous["Confidence"],
                    "Explanation": (
                        f"{ambiguous['Candidate Count']} unresolved Infinium rows share this "
                        f"row's normalized PO and/or invoice ({ambiguous['Candidate Infinium Row IDs']}). "
                        "The program will not guess which one, if any, corresponds to this row, "
                        "so nothing is posted pending documented review."
                    ),
                }
            )
    if reference_hold_analysis is not None and not reference_hold_analysis.empty:
        for hold in reference_hold_analysis.to_dict("records"):
            rows.append(
                {
                    "Section": REFERENCE_HOLD_SECTION,
                    "Match ID": hold["Hold ID"],
                    "Match Result": hold["Classification"],
                    "Reason Code": hold["Reason Code"],
                    "QB Index": int(hold["QuickBooks Row Index"]),
                    "Infinium Index": None,
                    "QB Record Scope": "Primary",
                    "Infinium Record Scope": None,
                    "Group Sequence": 1,
                    "Confidence": hold["Confidence"],
                    "Explanation": hold["Explanation"],
                }
            )
    for group in fuzzy_review_hold_groups:
        ordered_q = sorted(group.qb_rows, key=lambda idx: qb.at[idx, SOURCE_POS])
        ordered_i = sorted(group.inf_rows, key=lambda idx: inf.at[idx, SOURCE_POS])
        grouped = len(ordered_q) > 1 or len(ordered_i) > 1
        classification = (
            FUZZY_MATCH_CLASSIFICATION_GROUPED if grouped else FUZZY_MATCH_CLASSIFICATION_SINGLE
        )
        for sequence, (qidx, iidx) in enumerate(zip_longest(ordered_q, ordered_i), 1):
            rows.append(
                {
                    "Section": "09 Fuzzy Match Review Hold",
                    "Match ID": group.match_id,
                    "Match Result": classification,
                    "QB Index": qidx,
                    "Infinium Index": iidx,
                    "QB Record Scope": "Primary" if qidx is not None else None,
                    "Infinium Record Scope": "Primary" if iidx is not None else None,
                    "Group Sequence": sequence,
                    "Confidence": FUZZY_MATCH_CONFIDENCE,
                    "Explanation": (
                        f"{group.explanation} Neither amount is automatically posted "
                        "pending documented review."
                    ),
                }
            )

    def duplicate_lookup(report: Optional[pd.DataFrame]) -> dict[str, dict[str, Any]]:
        if report is None or report.empty:
            return {}
        primary = report.loc[report["Source Scope"].eq("Primary")].copy()
        if primary.empty:
            return {}
        primary["__STAGE_ORDER"] = primary["Screening Stage"].map(
            {"Same-file": 0, "Cross-scope": 1}
        ).fillna(9)
        primary = primary.sort_values(
            ["Source Row ID", "__STAGE_ORDER"], kind="stable"
        )
        return {
            str(source_id): group.iloc[0].to_dict()
            for source_id, group in primary.groupby("Source Row ID", sort=False)
        }

    qb_duplicate_detail = duplicate_lookup(qb_duplicate_report)
    inf_duplicate_detail = duplicate_lookup(inf_duplicate_report)
    variance_detail = (
        amount_variance_analysis.set_index("Variance ID").to_dict("index")
        if not amount_variance_analysis.empty else {}
    )
    ambiguous_detail = (
        ambiguous_duplicate_analysis.set_index("Ambiguous ID").to_dict("index")
        if not ambiguous_duplicate_analysis.empty else {}
    )
    po_reuse_qb_map = po_reuse_error_qb_index_map(po_reuse_errors)
    po_reuse_detail = (
        po_reuse_errors.set_index("PO Reuse ID").to_dict("index")
        if not po_reuse_errors.empty else {}
    )
    reference_hold_detail = (
        reference_hold_analysis.set_index("Hold ID").to_dict("index")
        if reference_hold_analysis is not None and not reference_hold_analysis.empty else {}
    )
    unresolved_q_indexes = [
        int(row["QB Index"])
        for row in rows
        if row.get("Section") == "02 Unmatched QuickBooks"
        and row.get("QB Index") is not None
    ]
    unresolved_invoice_groups: dict[str, list[int]] = defaultdict(list)
    for idx in unresolved_q_indexes:
        if qb.at[idx, NORM_INV]:
            unresolved_invoice_groups[qb.at[idx, NORM_INV]].append(idx)

    def group_lacks_matching_infinium_total(
        field: str,
        value: str,
        qb_indexes: list[int],
    ) -> bool:
        qb_total = _amount_total(qb, qb_indexes)
        inf_indexes = [
            int(idx) for idx in inf.index
            if inf.at[idx, field] == value and valid_cents(inf.at[idx, AMOUNT_CENTS])
        ]
        return not any(
            int(inf.at[idx, AMOUNT_CENTS]) == qb_total for idx in inf_indexes
        )

    for row in rows:
        section = str(row.get("Section", ""))
        row.update({
            "Exception Cause": "Not an exception",
            "Cause Confidence": row.get("Confidence", ""),
            "Financial Treatment": "No exception accrual treatment",
            "Related Source Row IDs": "",
            "Duplicate Basis": "",
            "Potential Duplicate Reason": "",
            "Differing Confirmation Fields": "",
            "Duplicate Values": "",
            "Duplicate Group ID": "",
            "Confirmed Copy Set ID": "",
            "Canonical Source Row ID": "",
            "Confirmation Basis": "",
            "Potential Amount Difference": None,
        })
        if section == "02 Unmatched QuickBooks":
            qidx = int(row["QB Index"])
            q_invoice = qb.at[qidx, NORM_INV]
            invoice_group = unresolved_invoice_groups.get(q_invoice, [])
            po_reuse_id = po_reuse_qb_map.get(qidx)
            if (
                q_invoice
                and len(invoice_group) > 1
                and group_lacks_matching_infinium_total(
                    NORM_INV, q_invoice, invoice_group
                )
            ):
                row["Exception Cause"] = (
                    "True Unmatched - Repeated Invoice numbers with no supporting "
                    "Infinium record"
                )
            elif po_reuse_id:
                # A normalized PO reused across 2+ still-unresolved QuickBooks
                # rows whose grouped total does not tie exactly to the grouped
                # Infinium total for that PO -- see build_po_reuse_errors. These
                # rows stay in the accrual; this only makes the classification
                # and its grouped detail traceable instead of leaving several
                # undifferentiated individual exceptions.
                po_detail = po_reuse_detail.get(po_reuse_id, {})
                row["Exception Cause"] = (
                    "PO Re-use Error - Repeated PO values do not net to a "
                    "matching Infinium total"
                )
                row["Related Source Row IDs"] = str(po_detail.get("Infinium Row IDs", ""))
                row["Potential Amount Difference"] = po_detail.get("Difference")
            else:
                row["Exception Cause"] = candidate_cause.get(
                    qb.at[qidx, QB_ID], "Unresolved matching exception"
                )
            row["Cause Confidence"] = "Review"
            row["Financial Treatment"] = "Included in provisional QuickBooks accrual support"
        elif section == "03 Unmatched Infinium":
            row["Exception Cause"] = "No corresponding QuickBooks record"
            row["Cause Confidence"] = "Review"
            row["Financial Treatment"] = "Informational; Infinium rows do not feed the QuickBooks accrual"
        elif section in {"04 Duplicate QuickBooks", "05 Duplicate Infinium"}:
            row["Exception Cause"] = "Confirmed Duplicate - Identical transaction copy"
            row["Cause Confidence"] = "High"
            row["Financial Treatment"] = (
                "Excluded from matching and automatic JE; canonical row retained"
            )
        elif section in {
            "06 Duplicate Review Hold QuickBooks",
            "07 Duplicate Review Hold Infinium",
        }:
            row["Exception Cause"] = "Potential Duplicate - Insufficient evidence for automatic exclusion"
            row["Cause Confidence"] = "Hold"
            row["Financial Treatment"] = "Excluded pending documented duplicate disposition"
        elif section == "08 Reference-Matched Amount Variance Review Hold":
            detail = variance_detail.get(str(row.get("Match ID", "")), {})
            row["Exception Cause"] = (
                "Potential Typo - Matching PO and/or Invoice values have different amounts"
            )
            row["Cause Confidence"] = str(detail.get("Confidence", "Review"))
            row["Financial Treatment"] = str(detail.get("Accrual Treatment", "Review hold"))
            row["Related Source Row IDs"] = "; ".join(
                filter(None, [
                    str(detail.get("QuickBooks Row ID", "")),
                    str(detail.get("Infinium Row ID", "")),
                ])
            )
            row["Potential Amount Difference"] = detail.get("Potential Difference")
        elif section == REFERENCE_HOLD_SECTION:
            hold_detail = reference_hold_detail.get(str(row.get("Match ID", "")), {})
            hold_code = str(hold_detail.get("Reason Code", ""))
            if hold_code == HOLD_PO_REUSE:
                row["Exception Cause"] = (
                    "PO Re-use Error - Repeated PO values do not net to a matching Infinium total"
                )
            elif hold_code == HOLD_HISTORICAL_CLEARANCE:
                row["Exception Cause"] = "Potential Match - Supporting historical Infinium record is withheld"
            else:
                row["Exception Cause"] = str(
                    candidate_cause.get(
                        qb.at[int(row["QB Index"]), QB_ID], hold_detail.get("Classification", "Review hold"),
                    )
                )
            row["Cause Confidence"] = "Review"
            row["Financial Treatment"] = str(hold_detail.get("Accrual Treatment", "Review hold"))
            row["Related Source Row IDs"] = "; ".join(
                filter(None, [
                    str(hold_detail.get("Related QuickBooks Row IDs") or ""),
                    str(hold_detail.get("Related Infinium Row IDs") or ""),
                ])
            )
            row["Potential Amount Difference"] = hold_detail.get("Amount Difference")
        elif section == "09 Fuzzy Match Review Hold":
            row["Exception Cause"] = "Potential Match - Pending Manual Confirmation"
            row["Cause Confidence"] = FUZZY_MATCH_CONFIDENCE
            row["Financial Treatment"] = "Excluded pending documented match confirmation"
        elif section == "10 Ambiguous Duplicate QuickBooks":
            ambiguous_row_detail = ambiguous_detail.get(str(row.get("Match ID", "")), {})
            row["Exception Cause"] = "Ambiguous Duplicate - Multiple records share the PO and/or Invoice"
            row["Cause Confidence"] = str(ambiguous_row_detail.get("Confidence", "Review"))
            row["Financial Treatment"] = str(ambiguous_row_detail.get("Accrual Treatment", "Review hold"))
            row["Related Source Row IDs"] = str(ambiguous_row_detail.get("Candidate Infinium Row IDs", ""))
            row["Potential Duplicate Reason"] = str(ambiguous_row_detail.get("Explanation", ""))

        detail: dict[str, Any] = {}
        if row.get("QB Index") is not None and row.get("QB Record Scope") == "Primary":
            detail = qb_duplicate_detail.get(str(qb.at[row["QB Index"], QB_ID]), {})
        if not detail and row.get("Infinium Index") is not None and row.get("Infinium Record Scope") == "Primary":
            detail = inf_duplicate_detail.get(str(inf.at[row["Infinium Index"], INF_ID]), {})
        if detail:
            def clean_detail_value(value: Any) -> str:
                return "" if value is None or pd.isna(value) else str(value)

            related = [
                clean_detail_value(detail.get("Other Source Row IDs In Group")),
                clean_detail_value(detail.get("Reference Source Row IDs")),
            ]
            row["Related Source Row IDs"] = "; ".join(
                value for value in related if value
            )
            row["Duplicate Basis"] = clean_detail_value(detail.get("Duplicate Basis"))
            row["Potential Duplicate Reason"] = clean_detail_value(
                detail.get("Potential Duplicate Reason")
            )
            row["Differing Confirmation Fields"] = clean_detail_value(
                detail.get("Differing Confirmation Fields")
            )
            row["Duplicate Values"] = clean_detail_value(detail.get("Duplicate Values"))
            row["Duplicate Group ID"] = clean_detail_value(detail.get("Duplicate Group ID"))
            row["Confirmed Copy Set ID"] = clean_detail_value(detail.get("Confirmed Copy Set ID"))
            row["Canonical Source Row ID"] = clean_detail_value(
                detail.get("Canonical Source Row ID")
            )
            row["Confirmation Basis"] = clean_detail_value(detail.get("Confirmation Basis"))
            if section in {
                "06 Duplicate Review Hold QuickBooks",
                "07 Duplicate Review Hold Infinium",
            }:
                basis = row["Duplicate Basis"]
                if basis == "Invoice + Amount (PO blank on both rows)":
                    row["Exception Cause"] = (
                        "Potential Duplicate - Repeated Invoice numbers do not net to a "
                        "matching opposing-system value"
                    )
                elif basis == "PO + Amount (invoice blank on both rows)":
                    row["Exception Cause"] = (
                        "Potential Duplicate - Repeated PO values do not net to a "
                        "matching opposing-system value"
                    )
                elif basis == "PO + Invoice + Amount":
                    row["Exception Cause"] = (
                        "Potential Duplicate - PO, Invoice, and Amount match but "
                        "other source fields differ"
                    )
    apply_referenced_match_references(
        rows, match_register if match_register is not None else pd.DataFrame(), candidates, qb,
    )
    return rows


def build_match_assessments(
    matches: list[MatchGroup],
    historical_clearances: pd.DataFrame,
    unmatched_qb: list[int],
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    candidates: pd.DataFrame,
    amount_variance_analysis: Optional[pd.DataFrame] = None,
    ambiguous_duplicate_analysis: Optional[pd.DataFrame] = None,
    reference_hold_analysis: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    candidate_map = candidates.set_index("QuickBooks Row ID").to_dict("index") if not candidates.empty else {}
    for group in matches:
        q_po = {qb.at[idx, NORM_PO] for idx in group.qb_rows if qb.at[idx, NORM_PO]}
        i_po = {inf.at[idx, NORM_PO] for idx in group.inf_rows if inf.at[idx, NORM_PO]}
        q_inv = {qb.at[idx, NORM_INV] for idx in group.qb_rows if qb.at[idx, NORM_INV]}
        i_inv = {inf.at[idx, NORM_INV] for idx in group.inf_rows if inf.at[idx, NORM_INV]}
        q_total = _amount_total(qb, group.qb_rows)
        i_total = _amount_total(inf, group.inf_rows)
        records.append(
            {
                "Match ID": group.match_id,
                "Decision": "Matched",
                "Match Ref.": group.match_ref,
                "Match Method": group.method,
                "Referenced Match Ref.": "",
                "Confidence": group.confidence,
                "QuickBooks Row Count": len(group.qb_rows),
                "Infinium Row Count": len(group.inf_rows),
                "QuickBooks Row IDs": "; ".join(qb.at[idx, QB_ID] for idx in group.qb_rows),
                "Infinium Row IDs": "; ".join(inf.at[idx, INF_ID] for idx in group.inf_rows),
                "PO Criterion": "Agree" if q_po and q_po == i_po else "Not used / differs",
                "Invoice Criterion": "Agree" if q_inv and q_inv == i_inv else "Not used / differs",
                "Signed Amount Criterion": "Agree" if q_total == i_total else "Differs",
                "QuickBooks Amount": cents_to_float(q_total),
                "Infinium Amount": cents_to_float(i_total),
                "Amount Difference": cents_to_float(q_total - i_total),
                "Group-Level Match": group.group_level,
                "Assessment Explanation": group.explanation,
            }
        )
    if not historical_clearances.empty:
        for _, clearance_group in historical_clearances.groupby(
            "Clearance ID", sort=False, dropna=False
        ):
            clearance = clearance_group.iloc[0]
            primary_is_qb = clearance["Primary Dataset"] == "QuickBooks Primary"
            primary_ids = [
                str(value) for value in clearance_group["Primary Row ID"] if value
            ]
            secondary_ids = [
                str(value) for value in clearance_group["Secondary Row ID"] if value
            ]
            primary_total = float(clearance_group["Primary Amount"].sum())
            secondary_total = float(clearance_group["Secondary Amount"].sum())
            records.append(
                {
                    "Match ID": clearance["Clearance ID"],
                    "Decision": "Matched - Historical Clearance",
                    "Match Ref.": clearance.get("Match Ref.", ""),
                    "Match Method": clearance["Match Method"],
                    "Referenced Match Ref.": "",
                    "Confidence": clearance["Confidence"],
                    "QuickBooks Row Count": (
                        len(primary_ids) if primary_is_qb else len(secondary_ids)
                    ),
                    "Infinium Row Count": (
                        len(secondary_ids) if primary_is_qb else len(primary_ids)
                    ),
                    "QuickBooks Row IDs": "; ".join(
                        primary_ids if primary_is_qb else secondary_ids
                    ),
                    "Infinium Row IDs": "; ".join(
                        secondary_ids if primary_is_qb else primary_ids
                    ),
                    "PO Criterion": "Applied under controlled historical pass",
                    "Invoice Criterion": "Applied under controlled historical pass",
                    "Signed Amount Criterion": "Agree",
                    "QuickBooks Amount": primary_total if primary_is_qb else secondary_total,
                    "Infinium Amount": secondary_total if primary_is_qb else primary_total,
                    "Amount Difference": float(clearance_group["Amount Difference"].sum()),
                    "Group-Level Match": bool(clearance["Group-Level Match"]),
                    "Assessment Explanation": clearance["Disposition"],
                }
            )
    for qidx in unmatched_qb:
        qid = qb.at[qidx, QB_ID]
        candidate = candidate_map.get(qid, {})
        records.append(
            {
                "Match ID": "",
                "Decision": "Unresolved",
                "Match Ref.": "",
                "Match Method": candidate.get("Disposition", "No match"),
                "Referenced Match Ref.": candidate.get("Referenced Match Ref.", ""),
                "Confidence": "Review",
                "QuickBooks Row Count": 1,
                "Infinium Row Count": candidate.get("Available Candidate Count", 0),
                "QuickBooks Row IDs": qid,
                "Infinium Row IDs": candidate.get("Available Infinium Candidate IDs", ""),
                "PO Criterion": "Candidate search performed" if qb.at[qidx, NORM_PO] else "Missing",
                "Invoice Criterion": "Candidate search performed" if qb.at[qidx, NORM_INV] else "Missing",
                "Signed Amount Criterion": "Valid" if valid_cents(qb.at[qidx, AMOUNT_CENTS]) else "Invalid / missing",
                "QuickBooks Amount": cents_to_float(qb.at[qidx, AMOUNT_CENTS]),
                "Infinium Amount": None,
                "Amount Difference": candidate.get("Minimum Amount Difference"),
                "Group-Level Match": False,
                "Assessment Explanation": "No reference evidence in Infinium supports this row.",
            }
        )
    if reference_hold_analysis is not None and not reference_hold_analysis.empty:
        for hold in reference_hold_analysis.to_dict("records"):
            candidate = candidate_map.get(hold["QuickBooks Row ID"], {})
            records.append(
                {
                    "Match ID": hold["Hold ID"],
                    "Decision": "Review Hold - Reference Evidence",
                    "Match Ref.": "",
                    "Match Method": hold["Classification"],
                    "Referenced Match Ref.": hold["Related Match Ref."],
                    "Confidence": hold["Confidence"],
                    "QuickBooks Row Count": 1,
                    "Infinium Row Count": candidate.get("Total Candidate Count", 0),
                    "QuickBooks Row IDs": hold["QuickBooks Row ID"],
                    "Infinium Row IDs": hold["Related Infinium Row IDs"],
                    "PO Criterion": "Candidate search performed" if hold["Normalized PO"] else "Missing",
                    "Invoice Criterion": "Candidate search performed" if hold["Normalized Invoice"] else "Missing",
                    "Signed Amount Criterion": (
                        "Differs - review required" if hold["Reason Code"] == HOLD_AMOUNT_VARIANCE
                        else "Not evaluated - correspondence cannot be safely determined"
                    ),
                    "QuickBooks Amount": hold["QuickBooks Amount"],
                    "Infinium Amount": hold["Infinium Amount"],
                    "Amount Difference": hold["Amount Difference"],
                    "Group-Level Match": False,
                    "Assessment Explanation": hold["Explanation"],
                }
            )
    if amount_variance_analysis is not None and not amount_variance_analysis.empty:
        for variance in amount_variance_analysis.to_dict("records"):
            records.append(
                {
                    "Match ID": variance["Variance ID"],
                    "Decision": "Reference-Matched Amount Variance Review Hold",
                    "Match Ref.": "",
                    "Match Method": variance["Reference Evidence"],
                    "Referenced Match Ref.": "",
                    "Confidence": variance["Confidence"],
                    "QuickBooks Row Count": 1,
                    "Infinium Row Count": 1,
                    "QuickBooks Row IDs": variance["QuickBooks Row ID"],
                    "Infinium Row IDs": variance["Infinium Row ID"],
                    "PO Criterion": (
                        "Agree" if variance["Normalized PO"] else "Not used / differs"
                    ),
                    "Invoice Criterion": (
                        "Agree" if variance["Normalized Invoice"] else "Not used / differs"
                    ),
                    "Signed Amount Criterion": "Differs - review required",
                    "QuickBooks Amount": variance["QuickBooks Amount"],
                    "Infinium Amount": variance["Infinium Amount"],
                    "Amount Difference": variance["Potential Difference"],
                    "Group-Level Match": False,
                    "Assessment Explanation": variance["Explanation"],
                }
            )
    if ambiguous_duplicate_analysis is not None and not ambiguous_duplicate_analysis.empty:
        for ambiguous in ambiguous_duplicate_analysis.to_dict("records"):
            records.append(
                {
                    "Match ID": ambiguous["Ambiguous ID"],
                    "Decision": "Ambiguous Duplicate - Multiple Candidates",
                    "Match Ref.": "",
                    "Match Method": ambiguous["Classification"],
                    "Referenced Match Ref.": "",
                    "Confidence": ambiguous["Confidence"],
                    "QuickBooks Row Count": 1,
                    "Infinium Row Count": ambiguous["Candidate Count"],
                    "QuickBooks Row IDs": ambiguous["QuickBooks Row ID"],
                    "Infinium Row IDs": ambiguous["Candidate Infinium Row IDs"],
                    "PO Criterion": "Agree" if ambiguous["Normalized PO"] else "Not used / differs",
                    "Invoice Criterion": "Agree" if ambiguous["Normalized Invoice"] else "Not used / differs",
                    "Signed Amount Criterion": "Not evaluated - correspondence is ambiguous",
                    "QuickBooks Amount": ambiguous["QuickBooks Amount"],
                    "Infinium Amount": None,
                    "Amount Difference": None,
                    "Group-Level Match": False,
                    "Assessment Explanation": ambiguous["Explanation"],
                }
            )
    return pd.DataFrame(records)
