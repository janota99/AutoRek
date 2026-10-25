"""Pre-match input checks: did every uploaded row, and every dollar, arrive where the matching will read it?

Streamlit-free and read-only. It looks at the frames the app already built (what the import cleanup kept, and what
the matching will actually read) and reports; it never changes a row, a mapping, or a match. The checks add to
``ingestion.build_source_validation_report``; they do not replace it.

Statuses follow that report: PASS, WARNING (look at it, the run can continue), FAIL (the run is blocked). Only the
row tie-out can FAIL: it fails when rows in the file are not accounted for, which would mean the import dropped
something it did not report.

Money is whole signed cents (``parse_amount_cents``), never a float comparison.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

import pandas as pd

from .matching import parse_amount_cents

COLUMNS = ["Dataset", "Check", "Status", "Details"]
NO_FILE = "NONE"           # the hash the uploader helper reports for an empty upload slot


@dataclass(frozen=True)
class SourceInput:
    """One uploaded file as the app holds it.

    ``imported`` is the frame after import cleanup (blank rows, report timestamps and repeated headers removed);
    ``used`` is what the matching reads (for QuickBooks, also without subtotal and incomplete rows).
    """
    label: str
    imported: pd.DataFrame
    used: pd.DataFrame
    amount_column: Optional[str]
    sha256: str


def _usd(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def _cents(series: pd.Series) -> pd.Series:
    return series.map(parse_amount_cents)


def _total_cents(frame: pd.DataFrame, column: Optional[str]) -> int:
    if not column or column not in frame.columns:
        return 0
    return int(sum(int(c) for c in _cents(frame[column]) if pd.notna(c)))


def _row_tie_out(src: SourceInput, add) -> None:
    audit = src.imported.attrs.get("ingestion_audit", {})
    file_rows = audit.get("source_rows_below_header")
    if file_rows is None:
        return
    blank = int(audit.get("blank_rows_removed", 0) or 0)
    stamps = int(audit.get("standalone_timestamp_rows_removed", 0) or 0)
    headers = int(audit.get("repeated_header_rows_removed", 0) or 0)
    kept = len(src.imported)
    excluded = kept - len(src.used)
    accounted = kept + blank + stamps + headers
    removed = [f"{n:,} {what}" for n, what in ((blank, "blank"), (stamps, "report timestamp"),
                                               (headers, "repeated header")) if n]
    if excluded:
        removed.append(f"{excluded:,} subtotal or incomplete")
    detail = (f"{file_rows:,} row(s) below the header: {len(src.used):,} used"
              + (", " + ", ".join(removed) + " removed." if removed else ", none removed."))
    if accounted == file_rows:
        add(src, "Rows in the file are all accounted for", "PASS", detail)
    else:
        add(src, "Rows in the file are all accounted for", "FAIL",
            f"{file_rows:,} row(s) below the header but only {accounted:,} accounted for "
            f"({file_rows - accounted:+,} unexplained). {detail}")


def _excluded_amount(src: SourceInput, add) -> None:
    excluded = len(src.imported) - len(src.used)
    if excluded <= 0:
        return
    cents = _total_cents(src.imported, src.amount_column) - _total_cents(src.used, src.amount_column)
    filter_audit = src.used.attrs.get("qb_subtotal_filter_audit", {})
    incomplete = int(filter_audit.get("incomplete_non_quantity_amount_rows_removed", 0) or 0)
    detail = f"{excluded:,} row(s) carrying {_usd(cents)} were left out of the reconciliation."
    if incomplete and cents:
        add(src, "Amount in rows left out", "WARNING",
            detail + f" {incomplete:,} of them were left out for a blank field rather than for being a labelled "
                     "total. Confirm none of them is a real transaction.")
    else:
        add(src, "Amount in rows left out", "PASS", detail + " They are labelled totals or carry no amount.")


def _amount_quality(src: SourceInput, add) -> None:
    column = src.amount_column
    if not column or column not in src.used.columns or src.used.empty:
        return
    raw = src.used[column]
    text = raw.astype("string").str.strip()
    blank = raw.isna() | text.fillna("").eq("")
    cents = _cents(raw)
    unreadable = ~blank & cents.isna()
    valid = cents.dropna().astype(int)
    zero = int((valid == 0).sum())
    credits = valid[valid < 0]
    parts = [f"{int(blank.sum()):,} blank", f"{int(unreadable.sum()):,} unreadable", f"{zero:,} zero"]
    add(src, "Amounts: blank, unreadable, zero",
        "WARNING" if blank.any() or unreadable.any() else "PASS",
        f"Of {len(src.used):,} row(s): " + ", ".join(parts) + ". Blank and unreadable amounts stay unreconciled "
        "and are listed for review; they are never treated as zero.")
    add(src, "Credits and negative amounts", "PASS",
        f"{len(credits):,} negative amount(s) totalling {_usd(int(credits.sum()))}."
        if len(credits) else "No negative amounts.")


def _file_identity(sources: Sequence[SourceInput], saved_runs: Mapping[str, str], add) -> None:
    present = [s for s in sources if s.sha256 and s.sha256 != NO_FILE]
    seen: dict[str, str] = {}
    shared = False
    for src in present:
        if src.sha256 in seen:
            shared = True
            add(None, "Different files uploaded", "WARNING",
                f"{seen[src.sha256]} and {src.label} are the same file (same SHA-256). "
                "One side of the reconciliation is being matched against itself.")
        else:
            seen[src.sha256] = src.label
    if not shared and len(present) > 1:
        add(None, "Different files uploaded", "PASS", "Every uploaded file is different.")
    for src in present:
        run = saved_runs.get(src.sha256)
        if run:
            add(src, "File not used before", "WARNING",
                f"This exact file was already used in saved run {run}. Running it again is fine if intended; "
                "otherwise this may be an old upload.")


def build_input_checks(sources: Sequence[SourceInput], saved_runs: Optional[Mapping[str, str]] = None) -> pd.DataFrame:
    """The pre-match input checks for every uploaded file, in the validation report's columns.

    ``saved_runs`` maps a file's SHA-256 to the saved run that used it (empty when there is no run history)."""
    records: list[dict[str, str]] = []

    def add(src: Optional[SourceInput], check: str, status: str, details: str) -> None:
        records.append({"Dataset": src.label if src else "All files", "Check": check,
                        "Status": status, "Details": details})

    for src in sources:
        _row_tie_out(src, add)
        _excluded_amount(src, add)
        _amount_quality(src, add)
    _file_identity(sources, saved_runs or {}, add)
    return pd.DataFrame(records, columns=COLUMNS)
