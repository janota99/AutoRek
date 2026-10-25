"""Review aids for what is left unmatched: same-amount rows, age, and concentration.

Read-only and Streamlit-free. It looks at the exception lists a finished run already produced and describes them;
it never creates, changes, or suggests a match, and ``MATCHING_RULE_VERSION`` does not depend on it.

* Same amount, no shared reference: how many unmatched rows have an unresolved row on the other side at the identical
  amount. Equal amounts are not evidence (``known-issues.md`` item 7), so this is context for a reviewer, not a
  pairing. It reports counts and dollars only, never "the" matching row.
* Age: how many fiscal periods old each unmatched QuickBooks row is, against the selected period.
* Concentration: unresolved QuickBooks value by customer, so one large exception stands out.

Money is whole signed cents; shares use absolute value so a credit never makes a share negative.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Optional

import pandas as pd

from ..duplicates import AMOUNT_CENTS
from .core import FISCAL_LABEL, cents_to_float, valid_cents

COLUMNS = ["Section", "Item", "Rows", "Amount", "Share of exceptions", "Note"]

SECTION_SAME_AMOUNT = "Same amount on the other side (review aid only)"
SECTION_AGE = "Age of unresolved QuickBooks items"
SECTION_CUSTOMER = "Unresolved QuickBooks value by customer"

SAME_AMOUNT_NOTE = ("Equal amounts are not evidence. The app never matches on amount alone, so these rows stay "
                    "unresolved until a PO or invoice supports a match.")
TOP_CUSTOMERS = 10
_LABEL = re.compile(r"^P(\d{2})-(\d{4})$")


def _cents(frame: pd.DataFrame, index: int) -> Optional[int]:
    value = frame.at[index, AMOUNT_CENTS]
    return int(value) if valid_cents(value) else None


def _row(section: str, item: str, cents_list: list[int], share: Optional[float] = None, note: str = "") -> dict[str, Any]:
    return {"Section": section, "Item": item, "Rows": len(cents_list),
            "Amount": cents_to_float(sum(cents_list)), "Share of exceptions": share, "Note": note}


def _same_amount_rows(side: str, frame: pd.DataFrame, indexes: list[int], other: pd.DataFrame,
                      other_indexes: list[int]) -> list[dict[str, Any]]:
    pool = Counter(c for c in (_cents(other, i) for i in other_indexes) if c)     # zero is not a meaningful match
    same: list[int] = []
    different: list[int] = []
    for idx in indexes:
        cents = _cents(frame, idx)
        if not cents:                                                             # blank, invalid, or zero
            continue
        (same if pool.get(cents) else different).append(cents)
    other_name = "Infinium" if side == "QuickBooks" else "QuickBooks"
    return [
        _row(SECTION_SAME_AMOUNT, f"Unmatched {side} rows with an unresolved {other_name} row at the same amount", same,
             note=SAME_AMOUNT_NOTE),
        _row(SECTION_SAME_AMOUNT, f"Unmatched {side} rows with no unresolved {other_name} row at that amount", different),
    ]


def _age_in_periods(label: Any, fiscal_period: int, fiscal_year: int) -> Optional[int]:
    found = _LABEL.match(str(label))
    if not found:
        return None
    period, year = int(found.group(1)), int(found.group(2))
    return (fiscal_year * 13 + fiscal_period) - (year * 13 + period)


def _age_rows(qb: pd.DataFrame, unmatched_qb: list[int], fiscal_period: Optional[int],
              fiscal_year: Optional[int]) -> list[dict[str, Any]]:
    if not fiscal_period or not fiscal_year:
        return [{"Section": SECTION_AGE, "Item": "Select a fiscal period to age the open items", "Rows": 0,
                 "Amount": 0.0, "Share of exceptions": None,
                 "Note": "Age is counted in fiscal periods back from the selected period."}]
    buckets: dict[str, list[int]] = {"Current period": [], "1 period old": [], "2 periods old": [],
                                     "3 or more periods old": [], "After the selected period": [],
                                     "Period not stated": []}
    for idx in unmatched_qb:
        cents = _cents(qb, idx) or 0
        age = _age_in_periods(qb.at[idx, FISCAL_LABEL], int(fiscal_period), int(fiscal_year))
        if age is None:
            key = "Period not stated"
        elif age < 0:
            key = "After the selected period"
        elif age >= 3:
            key = "3 or more periods old"
        else:
            key = "Current period" if age == 0 else f"{age} period{'s' if age > 1 else ''} old"
        buckets[key].append(cents)
    gross = sum(abs(c) for cents in buckets.values() for c in cents)
    return [_row(SECTION_AGE, name, cents, (sum(abs(c) for c in cents) / gross) if gross else None)
            for name, cents in buckets.items() if cents or name in ("Current period", "1 period old")]


def _customer_rows(qb: pd.DataFrame, unmatched_qb: list[int], customer_col: Optional[str]) -> list[dict[str, Any]]:
    if not customer_col or customer_col not in qb.columns:
        return [{"Section": SECTION_CUSTOMER, "Item": "No customer column is mapped", "Rows": 0, "Amount": 0.0,
                 "Share of exceptions": None, "Note": "Map the QuickBooks customer column to see concentration."}]
    names = qb[customer_col].astype("string").str.strip()
    by_customer: dict[str, list[int]] = {}
    for idx in unmatched_qb:
        name = names.get(idx)
        by_customer.setdefault(name if isinstance(name, str) and name else "(no customer name)", []).append(
            _cents(qb, idx) or 0)
    gross = sum(abs(c) for cents in by_customer.values() for c in cents)
    ranked = sorted(by_customer.items(), key=lambda item: (-sum(abs(c) for c in item[1]), item[0]))
    rows = [_row(SECTION_CUSTOMER, name, cents, (sum(abs(c) for c in cents) / gross) if gross else None)
            for name, cents in ranked[:TOP_CUSTOMERS]]
    rest = [c for _, cents in ranked[TOP_CUSTOMERS:] for c in cents]
    if rest:
        rows.append(_row(SECTION_CUSTOMER, f"All other customers ({len(ranked) - TOP_CUSTOMERS})", rest,
                         sum(abs(c) for c in rest) / gross if gross else None))
    if rows:
        rows[0]["Note"] = "Share is of the absolute value of all unresolved QuickBooks rows."
    return rows


def build_open_item_insights(
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    unmatched_qb: list[int],
    unmatched_inf: list[int],
    *,
    fiscal_period: Optional[int] = None,
    fiscal_year: Optional[int] = None,
    customer_col: Optional[str] = None,
) -> pd.DataFrame:
    """One table of review aids for the unresolved items of a finished run (no unresolved items: an empty table)."""
    if not unmatched_qb and not unmatched_inf:
        return pd.DataFrame(columns=COLUMNS)
    records: list[dict[str, Any]] = []
    records += _same_amount_rows("QuickBooks", qb, unmatched_qb, inf, unmatched_inf)
    records += _same_amount_rows("Infinium", inf, unmatched_inf, qb, unmatched_qb)
    if unmatched_qb:
        records += _age_rows(qb, unmatched_qb, fiscal_period, fiscal_year)
        records += _customer_rows(qb, unmatched_qb, customer_col)
    return pd.DataFrame(records, columns=COLUMNS)
