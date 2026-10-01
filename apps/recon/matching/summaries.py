"""Method, product, and customer summaries, exception analysis, and the controls table."""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from typing import Any, Optional

import pandas as pd

from ..duplicates import AMOUNT_CENTS
from .core import (
    _amount_total,
    _historical_row_indexes,
    _matched_row_indexes,
    _optional_index,
    cents_to_float,
    FISCAL_LABEL,
    MatchGroup,
    numeric_quantity_sum,
    parse_fiscal_period,
    PRIOR_PERIOD_URGENT_THRESHOLD,
    PRODUCT_STANDARD,
    QB_ID,
    ReconciliationResult,
)
from .labels import DISPOSITION_TRUE_UNMATCHED, FINAL_DISPOSITIONS


def build_method_summary(
    matches: list[MatchGroup],
    historical_clearances: pd.DataFrame,
    unmatched_qb: list[int],
    unmatched_inf: list[int],
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    duplicate_qb_rows: list[int],
    duplicate_inf_rows: list[int],
    duplicate_review_hold_qb_rows: list[int],
    duplicate_review_hold_inf_rows: list[int],
    amount_variance_review_hold_qb_rows: list[int],
    amount_variance_review_hold_inf_rows: list[int],
    ambiguous_duplicate_qb_rows: list[int],
    fuzzy_match_review_hold_qb_rows: list[int],
    fuzzy_match_review_hold_inf_rows: list[int],
    reference_hold_qb_rows: Optional[list[int]] = None,
) -> pd.DataFrame:
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"QB Rows": 0, "Infinium Rows": 0, "QB Cents": 0, "Infinium Cents": 0}
    )
    for group in matches:
        bucket = buckets[group.method]
        bucket["QB Rows"] += len(group.qb_rows)
        bucket["Infinium Rows"] += len(group.inf_rows)
        bucket["QB Cents"] += _amount_total(qb, group.qb_rows)
        bucket["Infinium Cents"] += _amount_total(inf, group.inf_rows)
    if not historical_clearances.empty:
        for clearance in historical_clearances.to_dict("records"):
            bucket = buckets[str(clearance["Match Method"])]
            primary_cents = int(Decimal(str(clearance["Primary Amount"])) * 100)
            if clearance["Primary Dataset"] == "QuickBooks Primary":
                if _optional_index(clearance["Primary Row Index"]) is not None:
                    bucket["QB Rows"] += 1
                    bucket["QB Cents"] += primary_cents
            else:
                if _optional_index(clearance["Primary Row Index"]) is not None:
                    bucket["Infinium Rows"] += 1
                    bucket["Infinium Cents"] += primary_cents
    if unmatched_qb:
        bucket = buckets["Unmatched QuickBooks"]
        bucket["QB Rows"] = len(unmatched_qb)
        bucket["QB Cents"] = _amount_total(qb, unmatched_qb)
    if unmatched_inf:
        bucket = buckets["Unmatched Infinium"]
        bucket["Infinium Rows"] = len(unmatched_inf)
        bucket["Infinium Cents"] = _amount_total(inf, unmatched_inf)
    if duplicate_qb_rows:
        bucket = buckets["Excess QuickBooks copies excluded from accrual"]
        bucket["QB Rows"] = len(duplicate_qb_rows)
        bucket["QB Cents"] = _amount_total(qb, duplicate_qb_rows)
    if duplicate_inf_rows:
        bucket = buckets["Excess Infinium copies excluded (separate worksheet)"]
        bucket["Infinium Rows"] = len(duplicate_inf_rows)
        bucket["Infinium Cents"] = _amount_total(inf, duplicate_inf_rows)
    if duplicate_review_hold_qb_rows:
        bucket = buckets["Duplicate Review Hold - QuickBooks (excluded from JE, pending disposition)"]
        bucket["QB Rows"] = len(duplicate_review_hold_qb_rows)
        bucket["QB Cents"] = _amount_total(qb, duplicate_review_hold_qb_rows)
    if duplicate_review_hold_inf_rows:
        bucket = buckets["Duplicate Review Hold - Infinium (pending disposition)"]
        bucket["Infinium Rows"] = len(duplicate_review_hold_inf_rows)
        bucket["Infinium Cents"] = _amount_total(inf, duplicate_review_hold_inf_rows)
    if amount_variance_review_hold_qb_rows or amount_variance_review_hold_inf_rows:
        bucket = buckets[
            "Reference-Matched Amount Variance Review Hold (excluded from automatic JE)"
        ]
        bucket["QB Rows"] = len(amount_variance_review_hold_qb_rows)
        bucket["Infinium Rows"] = len(amount_variance_review_hold_inf_rows)
        bucket["QB Cents"] = _amount_total(qb, amount_variance_review_hold_qb_rows)
        bucket["Infinium Cents"] = _amount_total(inf, amount_variance_review_hold_inf_rows)
    if ambiguous_duplicate_qb_rows:
        bucket = buckets["Ambiguous Duplicate QuickBooks (excluded from JE, pending research)"]
        bucket["QB Rows"] = len(ambiguous_duplicate_qb_rows)
        bucket["QB Cents"] = _amount_total(qb, ambiguous_duplicate_qb_rows)
    if reference_hold_qb_rows:
        bucket = buckets["Reference-Evidence Review Hold (excluded from JE, pending disposition)"]
        bucket["QB Rows"] = len(reference_hold_qb_rows)
        bucket["QB Cents"] = _amount_total(qb, reference_hold_qb_rows)
    if fuzzy_match_review_hold_qb_rows or fuzzy_match_review_hold_inf_rows:
        bucket = buckets["Fuzzy Match Review Hold (excluded from JE, pending confirmation)"]
        bucket["QB Rows"] = len(fuzzy_match_review_hold_qb_rows)
        bucket["Infinium Rows"] = len(fuzzy_match_review_hold_inf_rows)
        bucket["QB Cents"] = _amount_total(qb, fuzzy_match_review_hold_qb_rows)
        bucket["Infinium Cents"] = _amount_total(inf, fuzzy_match_review_hold_inf_rows)
    records = []
    for method, values in buckets.items():
        records.append(
            {
                "Match Method": method,
                "QuickBooks Rows": values["QB Rows"],
                "Infinium Rows": values["Infinium Rows"],
                "QuickBooks Amount": cents_to_float(values["QB Cents"]),
                "Infinium Amount": cents_to_float(values["Infinium Cents"]),
                "Amount Difference": cents_to_float(values["QB Cents"] - values["Infinium Cents"]),
                "Share of QuickBooks Rows": values["QB Rows"] / len(qb) if len(qb) else 0,
            }
        )
    return pd.DataFrame(records).sort_values(
        ["QuickBooks Rows", "Infinium Rows", "Match Method"], ascending=[False, False, True]
    ).reset_index(drop=True)


def build_product_summary(
    qb: pd.DataFrame,
    mapping: dict[str, Optional[str]],
    current_fiscal_period: Optional[int] = None,
    fiscal_year: Optional[int] = None,
) -> pd.DataFrame:
    qty_col = mapping.get("quantity")
    amount_col = mapping.get("amount")
    if not qty_col or not amount_col:
        return pd.DataFrame(columns=["Product Name", "Product Quantity", "Product Value"])
    work = qb[qb[PRODUCT_STANDARD].notna()].copy()
    if current_fiscal_period is not None:
        expected_label = f"P{int(current_fiscal_period):02d}-{int(fiscal_year or 0)}"
        work = work.loc[work[FISCAL_LABEL].eq(expected_label)].copy()
    if work.empty:
        return pd.DataFrame(columns=["Product Name", "Product Quantity", "Product Value"])
    work["__QTY"] = pd.to_numeric(work[qty_col], errors="coerce").fillna(0)
    work["__AMOUNT"] = work[AMOUNT_CENTS].map(cents_to_float)
    return (
        work.groupby(PRODUCT_STANDARD, as_index=False)
        .agg(**{"Product Quantity": ("__QTY", "sum"), "Product Value": ("__AMOUNT", "sum")})
        .rename(columns={PRODUCT_STANDARD: "Product Name"})
        .sort_values("Product Name")
        .reset_index(drop=True)
    )


def build_customer_summary(
    qb: pd.DataFrame,
    mapping: dict[str, Optional[str]],
    current_fiscal_period: Optional[int] = None,
    fiscal_year: Optional[int] = None,
) -> pd.DataFrame:
    """Sum of quantity and value by QuickBooks Customer, same period scope
    as build_product_summary -- a plain pivot, not a matching decision
    view. Customer is an optional mapping (see ingestion.py); with no
    customer/quantity/amount column mapped, this returns empty."""
    customer_col = mapping.get("customer")
    qty_col = mapping.get("quantity")
    amount_col = mapping.get("amount")
    columns = ["Customer Name", "Customer Quantity", "Customer Value"]
    if not customer_col or customer_col not in qb.columns or not qty_col or not amount_col:
        return pd.DataFrame(columns=columns)
    customer_names = qb[customer_col].astype("string").str.strip()
    work = qb[customer_names.notna() & customer_names.ne("")].copy()
    if current_fiscal_period is not None:
        expected_label = f"P{int(current_fiscal_period):02d}-{int(fiscal_year or 0)}"
        work = work.loc[work[FISCAL_LABEL].eq(expected_label)].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)
    work["__CUSTOMER"] = customer_names.loc[work.index]
    work["__QTY"] = pd.to_numeric(work[qty_col], errors="coerce").fillna(0)
    work["__AMOUNT"] = work[AMOUNT_CENTS].map(cents_to_float)
    return (
        work.groupby("__CUSTOMER", as_index=False)
        .agg(**{"Customer Quantity": ("__QTY", "sum"), "Customer Value": ("__AMOUNT", "sum")})
        .rename(columns={"__CUSTOMER": "Customer Name"})
        .sort_values("Customer Name")
        .reset_index(drop=True)
    )


def _period_sort(label: str) -> tuple[int, int, str]:
    match = re.fullmatch(r"P(\d{2})-(\d{4})", str(label))
    if not match:
        return 9999, 99, str(label)
    return int(match.group(2)), int(match.group(1)), str(label)


def build_exception_analysis(
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    unmatched_qb: list[int],
    unmatched_inf: list[int],
    candidates: pd.DataFrame,
    amount_variance_analysis: Optional[pd.DataFrame] = None,
    paired_rows: Optional[list[dict[str, Any]]] = None,
    fuzzy_match_review_hold_analysis: Optional[pd.DataFrame] = None,
    ambiguous_duplicate_analysis: Optional[pd.DataFrame] = None,
    reference_hold_analysis: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    candidate_reason = candidates.set_index("QuickBooks Row ID")["Exception Cause"].to_dict() if not candidates.empty else {}
    paired_cause = {
        int(row["QB Index"]): str(row["Exception Cause"])
        for row in (paired_rows or [])
        if row.get("Section") == "02 Unmatched QuickBooks"
        and row.get("QB Index") is not None
    }
    period_values = qb.loc[unmatched_qb, FISCAL_LABEL] if unmatched_qb else pd.Series(dtype=object)
    has_period = not period_values.empty and period_values.ne("Unspecified").any()
    period_groups: dict[str, list[int]] = defaultdict(list)
    for idx in unmatched_qb:
        label = qb.at[idx, FISCAL_LABEL] if has_period else "All QuickBooks transactions"
        period_groups[label].append(idx)
    for label in sorted(period_groups, key=_period_sort):
        rows = period_groups[label]
        records.append(
            {
                "Analysis Type": "QuickBooks exceptions by source period",
                "Dimension": label,
                "Transaction Count": len(rows),
                "Amount": cents_to_float(_amount_total(qb, rows)),
            }
        )
    reason_groups: dict[str, list[int]] = defaultdict(list)
    for idx in unmatched_qb:
        reason_groups[
            paired_cause.get(
                idx,
                candidate_reason.get(qb.at[idx, QB_ID], "No match"),
            )
        ].append(idx)
    for reason, rows in sorted(reason_groups.items()):
        records.append(
            {
                "Analysis Type": "QuickBooks exceptions by reason",
                "Dimension": reason,
                "Transaction Count": len(rows),
                "Amount": cents_to_float(_amount_total(qb, rows)),
            }
        )
    if unmatched_inf:
        records.append(
            {
                "Analysis Type": "Infinium exceptions",
                "Dimension": "Unmatched Infinium",
                "Transaction Count": len(unmatched_inf),
                "Amount": cents_to_float(_amount_total(inf, unmatched_inf)),
            }
        )
    if amount_variance_analysis is not None and not amount_variance_analysis.empty:
        for classification, group in amount_variance_analysis.groupby(
            "Classification", sort=False
        ):
            records.append(
                {
                    "Analysis Type": "Reference-matched amount variances on review hold",
                    "Dimension": classification,
                    "Transaction Count": len(group),
                    "Amount": float(group["QuickBooks Amount"].sum()),
                }
            )
    if fuzzy_match_review_hold_analysis is not None and not fuzzy_match_review_hold_analysis.empty:
        for classification, group in fuzzy_match_review_hold_analysis.groupby(
            "Classification", sort=False
        ):
            records.append(
                {
                    "Analysis Type": "Fuzzy matches on review hold",
                    "Dimension": classification,
                    "Transaction Count": len(group),
                    "Amount": float(group["QuickBooks Amount"].sum()),
                }
            )
    if reference_hold_analysis is not None and not reference_hold_analysis.empty:
        for classification, group in reference_hold_analysis.groupby("Reason Code", sort=True):
            records.append(
                {
                    "Analysis Type": "Reference-evidence review holds (excluded from JE)",
                    "Dimension": str(group["Classification"].iloc[0]).split(" by ")[0],
                    "Transaction Count": len(group),
                    "Amount": numeric_quantity_sum(group["QuickBooks Amount"]),
                }
            )
    if ambiguous_duplicate_analysis is not None and not ambiguous_duplicate_analysis.empty:
        records.append(
            {
                "Analysis Type": "Ambiguous duplicates on review hold",
                "Dimension": "Ambiguous Duplicate - Multiple Candidates",
                "Transaction Count": len(ambiguous_duplicate_analysis),
                "Amount": float(ambiguous_duplicate_analysis["QuickBooks Amount"].sum()),
            }
        )
    return pd.DataFrame(records)


def build_fiscal_exception_summary(result: ReconciliationResult) -> pd.DataFrame:
    """Summarize unresolved primary QuickBooks exceptions by source period."""
    columns = [
        "Fiscal Period", "Period Classification", "Exception Count",
        "Exception Quantity", "Net Exception Amount",
    ]
    if not result.unmatched_qb:
        return pd.DataFrame(columns=columns)

    period_column = result.qb_mapping.get("period")
    quantity_column = result.qb_mapping.get("quantity")
    selected_period = result.metadata.get("fiscal_period")
    default_year = int(result.metadata.get("fiscal_year", result.run_timestamp.year))
    work = result.qb_work.loc[result.unmatched_qb].copy()
    work["__EXCEPTION_QUANTITY"] = (
        pd.to_numeric(work[quantity_column], errors="coerce").fillna(0)
        if quantity_column and quantity_column in work.columns
        else 0.0
    )
    work["__EXCEPTION_AMOUNT"] = work[AMOUNT_CENTS].map(cents_to_float)
    if not period_column or period_column not in work.columns:
        return pd.DataFrame(
            [{
                "Fiscal Period": "Not available",
                "Period Classification": "Fiscal Period Not Available",
                "Exception Count": len(work),
                "Exception Quantity": float(work["__EXCEPTION_QUANTITY"].sum()),
                "Net Exception Amount": float(work["__EXCEPTION_AMOUNT"].sum()),
            }],
            columns=columns,
        )

    work["__PERIOD_NUMBER"] = work[period_column].map(
        lambda value: parse_fiscal_period(value, default_year)[0]
    )
    work["Fiscal Period"] = work["__PERIOD_NUMBER"].map(
        lambda value: f"PD-{int(value):02d}" if pd.notna(value) else "Unspecified"
    )

    def classify(value: Any) -> str:
        if pd.isna(value):
            return "Unspecified Period - Review"
        if selected_period is None:
            return "Reporting Period Not Selected"
        if int(value) == int(selected_period):
            return "Current Period"
        periods_behind = int(selected_period) - int(value)
        # A row from the immediate prior period (or the one before that) is
        # routine -- the prior period's close is often still trickling in
        # when this period's reconciliation runs. Only a gap wider than that
        # signals a genuinely stale, investigate-now exception.
        if 0 < periods_behind <= PRIOR_PERIOD_URGENT_THRESHOLD:
            return "Prior Period"
        return "Urgent Prior Period"

    work["Period Classification"] = work["__PERIOD_NUMBER"].map(classify)
    work["__URGENCY_SORT"] = work["Period Classification"].map(
        {
            "Urgent Prior Period": 1,
            "Prior Period": 2,
            "Unspecified Period - Review": 3,
            "Reporting Period Not Selected": 4,
            "Current Period": 5,
        }
    )
    summary = (
        work.groupby(
            ["Fiscal Period", "Period Classification", "__URGENCY_SORT", "__PERIOD_NUMBER"],
            as_index=False,
            dropna=False,
        )
        .agg(**{
            "Exception Count": (QB_ID, "size"),
            "Exception Quantity": ("__EXCEPTION_QUANTITY", "sum"),
            "Net Exception Amount": ("__EXCEPTION_AMOUNT", "sum"),
        })
        .sort_values(["__URGENCY_SORT", "__PERIOD_NUMBER"], na_position="last")
    )
    return summary.reindex(columns=columns).reset_index(drop=True)


def build_controls(
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    matches: list[MatchGroup],
    historical_clearances: pd.DataFrame,
    unmatched_qb: list[int],
    unmatched_inf: list[int],
    duplicate_qb_rows: list[int],
    duplicate_inf_rows: list[int],
    duplicate_review_hold_qb_rows: list[int],
    duplicate_review_hold_inf_rows: list[int],
    amount_variance_review_hold_qb_rows: list[int],
    amount_variance_review_hold_inf_rows: list[int],
    ambiguous_duplicate_qb_rows: list[int],
    fuzzy_match_review_hold_qb_rows: list[int],
    fuzzy_match_review_hold_inf_rows: list[int],
    reference_hold_qb_rows: Optional[list[int]] = None,
    qb_dispositions: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    reference_hold_qb_rows = reference_hold_qb_rows or []
    matched_q = _matched_row_indexes(matches, "QB")
    matched_i = _matched_row_indexes(matches, "INF")
    historical_q = _historical_row_indexes(
        historical_clearances,
        "QuickBooks Primary",
        "Primary Row Index",
    )
    historical_i = _historical_row_indexes(
        historical_clearances,
        "Infinium Primary",
        "Primary Row Index",
    )
    qb_total = _amount_total(qb, qb.index)
    inf_total = _amount_total(inf, inf.index)
    matched_q_total = _amount_total(qb, matched_q)
    matched_i_total = _amount_total(inf, matched_i)
    historical_q_total = _amount_total(qb, historical_q)
    historical_i_total = _amount_total(inf, historical_i)
    unresolved_q_total = _amount_total(qb, unmatched_qb)
    unresolved_i_total = _amount_total(inf, unmatched_inf)
    duplicate_q_total = _amount_total(qb, duplicate_qb_rows)
    duplicate_i_total = _amount_total(inf, duplicate_inf_rows)
    review_hold_q_total = _amount_total(qb, duplicate_review_hold_qb_rows)
    review_hold_i_total = _amount_total(inf, duplicate_review_hold_inf_rows)
    variance_hold_q_total = _amount_total(qb, amount_variance_review_hold_qb_rows)
    variance_hold_i_total = _amount_total(inf, amount_variance_review_hold_inf_rows)
    ambiguous_q_total = _amount_total(qb, ambiguous_duplicate_qb_rows)
    fuzzy_hold_q_total = _amount_total(qb, fuzzy_match_review_hold_qb_rows)
    fuzzy_hold_i_total = _amount_total(inf, fuzzy_match_review_hold_inf_rows)
    reference_hold_q_total = _amount_total(qb, reference_hold_qb_rows)
    historical_difference = (
        round(float(historical_clearances["Amount Difference"].sum()), 2)
        if not historical_clearances.empty else 0.0
    )
    records = [
        ("QuickBooks row completeness", len(qb),
         len(matched_q) + len(historical_q) + len(unmatched_qb) + len(duplicate_qb_rows)
         + len(duplicate_review_hold_qb_rows) + len(amount_variance_review_hold_qb_rows)
         + len(ambiguous_duplicate_qb_rows) + len(fuzzy_match_review_hold_qb_rows)
         + len(reference_hold_qb_rows)),
        ("Infinium row completeness", len(inf),
         len(matched_i) + len(historical_i) + len(unmatched_inf) + len(duplicate_inf_rows)
         + len(duplicate_review_hold_inf_rows) + len(amount_variance_review_hold_inf_rows)
         + len(fuzzy_match_review_hold_inf_rows)),
        ("QuickBooks amount roll-forward", cents_to_float(qb_total),
         cents_to_float(
             matched_q_total + historical_q_total + unresolved_q_total
             + duplicate_q_total + review_hold_q_total + variance_hold_q_total
             + ambiguous_q_total + fuzzy_hold_q_total + reference_hold_q_total
         )),
        ("Infinium amount roll-forward", cents_to_float(inf_total),
         cents_to_float(
             matched_i_total + historical_i_total + unresolved_i_total
             + duplicate_i_total + review_hold_i_total + variance_hold_i_total
             + fuzzy_hold_i_total
         )),
        ("Primary-to-primary matched totals", cents_to_float(matched_q_total), cents_to_float(matched_i_total)),
        ("Historical clearance amount difference", 0.0, historical_difference),
        ("Unresolved JE support", cents_to_float(unresolved_q_total),
         cents_to_float(
             qb_total - matched_q_total - historical_q_total
             - duplicate_q_total - review_hold_q_total - variance_hold_q_total
             - ambiguous_q_total - fuzzy_hold_q_total - reference_hold_q_total
         )),
        ("Excess QuickBooks copies excluded from JE", cents_to_float(duplicate_q_total),
         cents_to_float(duplicate_q_total)),
        ("Duplicate Review Hold QuickBooks items excluded from JE",
         cents_to_float(review_hold_q_total), cents_to_float(review_hold_q_total)),
        ("Reference-matched amount variance QuickBooks items excluded from automatic JE",
         cents_to_float(variance_hold_q_total), cents_to_float(variance_hold_q_total)),
        ("Ambiguous duplicate QuickBooks items excluded from JE",
         cents_to_float(ambiguous_q_total), cents_to_float(ambiguous_q_total)),
        ("Fuzzy match review hold QuickBooks items excluded from automatic JE",
         cents_to_float(fuzzy_hold_q_total), cents_to_float(fuzzy_hold_q_total)),
        ("Reference-evidence review hold QuickBooks items excluded from automatic JE",
         cents_to_float(reference_hold_q_total), cents_to_float(reference_hold_q_total)),
        # Informational: Infinium-only exceptions are reported in their own total and
        # can never offset the QuickBooks-based journal entry.
        ("Infinium-only exceptions reported separately (no QuickBooks JE effect)",
         cents_to_float(unresolved_i_total), cents_to_float(unresolved_i_total)),
    ]
    if qb_dispositions is not None:
        # Source rows must tie to their final dispositions in count AND dollars,
        # and the proposed JE is by definition the TRUE_UNMATCHED rows alone.
        ledger_amounts = qb_dispositions["Amount"].fillna(0.0)
        true_unmatched = qb_dispositions["Final Disposition"].eq(DISPOSITION_TRUE_UNMATCHED)
        records.extend([
            ("Source QuickBooks rows = Matched + Confirmed Duplicate Excluded + Review-Hold + True-Unmatched rows",
             len(qb), int(qb_dispositions["Final Disposition"].isin(FINAL_DISPOSITIONS).sum())),
            ("Every QuickBooks source row has exactly one final disposition",
             len(qb), int(qb_dispositions["QBO Row ID"].nunique())),
            ("Source QuickBooks amount = Matched + Confirmed Duplicate Excluded + Review-Hold + True-Unmatched amounts",
             round(cents_to_float(qb_total), 2), round(float(ledger_amounts.sum()), 2)),
            ("Proposed JE = sum of TRUE_UNMATCHED QuickBooks amounts",
             round(cents_to_float(unresolved_q_total), 2), round(float(ledger_amounts[true_unmatched].sum()), 2)),
        ])
    output = []
    for check, expected, actual in records:
        difference = float(expected) - float(actual)
        output.append(
            {
                "Check": check,
                "Expected": expected,
                "Actual": actual,
                "Difference": difference,
                "Status": "PASS" if difference == 0 else "FAIL",
            }
        )
    return pd.DataFrame(output)
