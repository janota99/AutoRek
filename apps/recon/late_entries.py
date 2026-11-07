"""Late and backdated entries: rows that reached a closed fiscal period after it should have closed.

Streamlit-free and read-only. It looks at frames the app already built and reports; it never changes a row, a
mapping, or a match, and ``MATCHING_RULE_VERSION`` does not depend on it. Two independent checks:

* ``find_late_entries`` (one pull): a row whose transaction date falls in a fiscal period earlier than the selected
  one, but whose created date is later than that period's end plus a visible number of close days. The created date is
  the evidence; the transaction date alone only says which period a row belongs to.
* ``compare_pulls`` (two pulls of the same period): an earlier export against a fresh one. Rows that appeared,
  disappeared, or changed amount or transaction date are listed, with the change in the period total. A row is paired
  as "changed" only when it is the single leftover on each side for its PO and invoice; anything less certain stays
  listed as removed plus added, so the report never guesses which rows belong together.

Money is whole signed cents (``parse_amount_cents``), never a float comparison.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Optional

import pandas as pd

from shared.fiscal_calendar import FiscalCalendar, validate
from .matching.core import clean_alphanumeric, clean_po, parse_amount_cents

CREATED_PATTERNS = ["CREATED", "CREATED DATE", "DATE CREATED", "CREATE DATE", "TIME CREATED", "ENTRY DATE",
                    "DATE ENTERED", "ENTERED", "CREATED ON", "CREATED AT"]
DEFAULT_CLOSE_DAYS = 5          # days after a period's end that entries are still expected; shown, never hidden
PRIOR_YEAR = "Prior fiscal year"

LATE_COLUMNS = ["Dataset", "PO", "Invoice", "Customer", "Amount", "Transaction date", "Transaction period",
                "Period end", "Created", "Days after period end"]
DIFF_COLUMNS = ["Change", "PO", "Invoice", "Customer", "Earlier amount", "Current amount", "Earlier date",
                "Current date", "Created", "Period"]

ADDED, REMOVED = "Added since the earlier pull", "Missing from the current pull"
AMOUNT_CHANGED, DATE_CHANGED = "Amount changed", "Transaction date changed"


def _usd(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def _text(value: Any) -> str:
    return "" if value is None or (not isinstance(value, str) and pd.isna(value)) else str(value).strip()


def to_dates(series: pd.Series) -> pd.Series:
    """Calendar days (midnight timestamps), NaT where unreadable. Excel serial numbers are read as Excel does."""
    numeric = pd.to_numeric(series, errors="coerce")
    is_date = series.map(lambda v: isinstance(v, (pd.Timestamp, date)))
    serial = numeric.between(20000, 80000) & ~is_date
    out = pd.to_datetime(series.where(~serial), errors="coerce", format="mixed")
    out = out.dt.tz_localize(None) if getattr(out.dt, "tz", None) is not None else out
    if serial.any():
        out = out.where(~serial, pd.to_datetime("1899-12-30") + pd.to_timedelta(numeric.where(serial), unit="D"))
    return out.dt.normalize()


def detect_created_column(columns: list[str]) -> Optional[str]:
    """The first column whose name is a known created-date name (exact, case-insensitive), else None."""
    by_name = {str(c).strip().upper(): c for c in columns}
    for pattern in CREATED_PATTERNS:
        if pattern in by_name:
            return by_name[pattern]
    return None


def _period_of(day: pd.Timestamp, calendar: FiscalCalendar) -> tuple[Optional[str], Optional[date], Optional[int]]:
    """(label, period end, period number) for a day; (None, None, None) after the calendar's last period.

    A day before the calendar's first day belongs to an earlier fiscal year whose period ends are not known here, so
    its end is taken as the day before this year starts. That is the latest it could be, so such a row is flagged only
    when it is late even by that generous measure."""
    d = day.date()
    if d < calendar.start:
        return PRIOR_YEAR, calendar.start - timedelta(days=1), 0
    for number, end in enumerate(calendar.period_ends, start=1):
        if d <= end:
            return f"P{number:02d}", end, number
    return None, None, None


@dataclass(frozen=True)
class LateEntryResult:
    rows: pd.DataFrame
    checked: int = 0                 # rows with a readable transaction date and created date
    unreadable_dates: int = 0
    unreadable_created: int = 0
    after_calendar: int = 0
    note: str = ""                   # why the check could not run, or "" when it ran
    status: str = "PASS"
    details: str = ""
    amount_cents: int = 0
    by_period: dict[str, int] = field(default_factory=dict)


def find_late_entries(
    frame: pd.DataFrame,
    *,
    dataset: str,
    mapping: Mapping[str, Optional[str]],
    created_column: Optional[str],
    calendar: FiscalCalendar,
    selected_period: Optional[int],
    close_days: int = DEFAULT_CLOSE_DAYS,
) -> LateEntryResult:
    """Rows created after the close of the earlier period their transaction date belongs to."""
    empty = pd.DataFrame(columns=LATE_COLUMNS)
    date_column = mapping.get("date")
    problem = validate(calendar)
    if problem:
        return LateEntryResult(empty, note=f"The fiscal calendar is not usable: {problem}", status="WARNING")
    if not date_column or date_column not in frame.columns:
        return LateEntryResult(empty, note=f"Map the {dataset} transaction date column to run this check.",
                               status="WARNING")
    if not created_column or created_column not in frame.columns:
        return LateEntryResult(empty, note=f"Choose the {dataset} created-date column to run this check.",
                               status="WARNING")

    txn = to_dates(frame[date_column])
    created = to_dates(frame[created_column])
    readable = txn.notna() & created.notna()
    index = txn.index[readable]
    info = pd.DataFrame([_period_of(d, calendar) for d in txn[index]], index=index, columns=["label", "end", "number"])
    label, end, number = info["label"], pd.to_datetime(info["end"]), pd.to_numeric(info["number"])

    in_scope = label.notna()
    if selected_period:
        in_scope &= number.lt(int(selected_period))               # closed relative to the period being reconciled
    deadline = end.where(in_scope) + pd.Timedelta(days=int(close_days))
    late = created[index].gt(deadline)

    rows = frame.loc[late[late].index]
    amounts = rows[mapping["amount"]].map(parse_amount_cents) if mapping.get("amount") in rows.columns else None

    def col(name: Optional[str]) -> pd.Series:
        return rows[name].map(_text) if name and name in rows.columns else pd.Series("", index=rows.index)

    out = pd.DataFrame({
        "Dataset": dataset, "PO": col(mapping.get("po")), "Invoice": col(mapping.get("invoice")),
        "Customer": col(mapping.get("customer")),
        "Amount": (amounts.map(lambda c: None if c is None or pd.isna(c) else c / 100) if amounts is not None
                   else pd.Series(None, index=rows.index)),
        "Transaction date": txn[rows.index].dt.date, "Transaction period": label[rows.index],
        "Period end": end[rows.index].dt.date, "Created": created[rows.index].dt.date,
        "Days after period end": (created[rows.index] - end[rows.index]).dt.days,
    }, columns=LATE_COLUMNS).sort_values("Days after period end", ascending=False, kind="stable")

    cents = 0 if amounts is None else int(sum(int(c) for c in amounts if c is not None and pd.notna(c)))
    unreadable_dates = int(txn.isna().sum())
    unreadable_created = int(created.isna().sum())
    after = int(label.isna().sum())                                  # dated after the calendar's last period
    counts = out["Transaction period"].value_counts().to_dict()
    scope = (f"transaction dates before Period {int(selected_period):02d}" if selected_period
             else "every transaction date in the fiscal calendar")
    base = (f"{int(readable.sum()):,} of {len(frame):,} row(s) have a readable transaction and created date "
            f"(checked against {scope}; {int(close_days)} close day(s) allowed after each period end).")
    if len(out):
        details = (f"{len(out):,} row(s) totalling {_usd(cents)} were created after the period they belong to had "
                   f"closed. {base}")
        status = "WARNING"
    else:
        details = f"No row was created after its period closed. {base}"
        status = "PASS"
    if unreadable_dates or unreadable_created:
        details += (f" {unreadable_dates:,} transaction date(s) and {unreadable_created:,} created date(s) could "
                    "not be read; those rows were not checked.")
        status = "WARNING"
    return LateEntryResult(out.reset_index(drop=True), int(readable.sum()), unreadable_dates, unreadable_created,
                           after, "", status, details, cents, {str(k): int(v) for k, v in counts.items()})


# ---------------------------------------------------------------------------------------------------------------------
# Two pulls of the same period
# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PullComparison:
    rows: pd.DataFrame
    counts: dict[str, int]
    earlier_cents: int
    current_cents: int
    note: str = ""
    status: str = "PASS"

    @property
    def drift_cents(self) -> int:
        return self.current_cents - self.earlier_cents

    @property
    def details(self) -> str:
        if self.note:
            return self.note
        parts = ", ".join(f"{n:,} {name.lower()}" for name, n in self.counts.items() if n and name != "Unchanged")
        head = (f"Period total moved from {_usd(self.earlier_cents)} to {_usd(self.current_cents)} "
                f"({'+' if self.drift_cents >= 0 else '-'}{_usd(abs(self.drift_cents))}). ")
        return head + (f"{parts}." if parts else "Every row is the same in both pulls.")


def _records(frame: pd.DataFrame, mapping: Mapping[str, Optional[str]], created_column: Optional[str]) -> list[dict]:
    def values(name: Optional[str]) -> list:
        return frame[name].tolist() if name and name in frame.columns else [None] * len(frame)

    dates = to_dates(frame[mapping["date"]]).tolist() if mapping.get("date") in frame.columns else [pd.NaT] * len(frame)
    created = (to_dates(frame[created_column]).tolist()
               if created_column and created_column in frame.columns else [pd.NaT] * len(frame))
    out = []
    for po, inv, raw, cust, per, d, c in zip(values(mapping.get("po")), values(mapping.get("invoice")),
                                             values(mapping["amount"]), values(mapping.get("customer")),
                                             values(mapping.get("period")), dates, created):
        cents = parse_amount_cents(raw)
        out.append({"pair": (clean_po(po), clean_alphanumeric(inv)),
                    "amount": cents if cents is not None else f"?{_text(raw)}",
                    "po": _text(po), "invoice": _text(inv), "customer": _text(cust), "period": _text(per),
                    "date": None if pd.isna(d) else d.date(), "created": None if pd.isna(c) else c.date()})
    return out


def _total(records: list[dict]) -> int:
    return sum(r["amount"] for r in records if isinstance(r["amount"], int))


def _shown(amount: Any) -> Optional[float]:
    return amount / 100 if isinstance(amount, int) else None


def compare_pulls(
    earlier: pd.DataFrame,
    current: pd.DataFrame,
    *,
    mapping: Mapping[str, Optional[str]],
    created_column: Optional[str] = None,
) -> PullComparison:
    """Rows that differ between an earlier pull and a fresh pull of the same period (same column names in both)."""
    missing = [name for name in (mapping.get("po"), mapping.get("invoice"), mapping.get("amount"))
               if name and (name not in earlier.columns or name not in current.columns)]
    if not mapping.get("amount") or missing:
        return PullComparison(pd.DataFrame(columns=DIFF_COLUMNS), {}, 0, 0, status="WARNING",
                              note="The two pulls do not share the mapped PO, invoice and amount columns, so they "
                                   "were not compared." + (f" Missing: {', '.join(missing)}." if missing else ""))
    old, new = _records(earlier, mapping, created_column), _records(current, mapping, created_column)

    by_key_old: dict[tuple, list[dict]] = defaultdict(list)
    by_key_new: dict[tuple, list[dict]] = defaultdict(list)
    for r in old:
        by_key_old[(r["pair"], r["amount"])].append(r)
    for r in new:
        by_key_new[(r["pair"], r["amount"])].append(r)

    changes: list[tuple[str, dict, dict]] = []                     # (change, earlier row or {}, current row or {})
    left_old: dict[tuple, list[dict]] = defaultdict(list)
    left_new: dict[tuple, list[dict]] = defaultdict(list)
    unchanged = 0
    for key in by_key_old.keys() | by_key_new.keys():
        a, b = by_key_old.get(key, []), by_key_new.get(key, [])
        paired = min(len(a), len(b))
        unchanged += paired
        if paired == 1 and a[0]["date"] != b[0]["date"] and (a[0]["date"] or b[0]["date"]):
            changes.append((DATE_CHANGED, a[0], b[0]))
        left_old[key[0]].extend(a[paired:])
        left_new[key[0]].extend(b[paired:])
    for pair in left_old.keys() | left_new.keys():
        a, b = left_old.get(pair, []), left_new.get(pair, [])
        if len(a) == 1 and len(b) == 1:
            changes.append((AMOUNT_CHANGED, a[0], b[0]))
            continue
        changes += [(REMOVED, r, {}) for r in a] + [(ADDED, {}, r) for r in b]

    def line(kind: str, a: dict, b: dict) -> dict:
        r = b or a
        return {"Change": kind, "PO": r["po"], "Invoice": r["invoice"], "Customer": r["customer"],
                "Earlier amount": _shown(a.get("amount")), "Current amount": _shown(b.get("amount")),
                "Earlier date": a.get("date"), "Current date": b.get("date"),
                "Created": b.get("created") or a.get("created"), "Period": r["period"]}

    order = {ADDED: 0, AMOUNT_CHANGED: 1, DATE_CHANGED: 2, REMOVED: 3}
    changes.sort(key=lambda c: (order[c[0]], (c[2] or c[1])["invoice"], (c[2] or c[1])["po"]))
    rows = pd.DataFrame([line(*c) for c in changes], columns=DIFF_COLUMNS)
    counts = {name: sum(1 for c in changes if c[0] == name) for name in (ADDED, AMOUNT_CHANGED, DATE_CHANGED, REMOVED)}
    counts["Unchanged"] = unchanged
    return PullComparison(rows, counts, _total(old), _total(new),
                          status="WARNING" if changes or _total(old) != _total(new) else "PASS")
