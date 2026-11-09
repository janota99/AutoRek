"""Operations' recurring "Raw Goods & Receivings" workbook: one file, both sources.

* ``RECEIVED LOG``: one row per delivery (header row: DESCRIPTION, ITEMS, COMPANY, DATE DELIVERED, LOT #,
  BOL #, SEAL #, PO #, QUANTITY, PRICE, PERIOD, INVOICE #). The material is the ITEMS column (B);
  DESCRIPTION holds a vendor or category and never decides it. PRICE (J) is the receipt's total cost;
  the unit cost is that divided by QUANTITY, which the engine does. The whole history is in the log, so
  receipts are filtered to the selected period by delivery date against the fiscal calendar.
* ``PERIOD END <year>``: one row per material, ending quantities in the ``Period 1`` ... ``Period 13``
  columns. Only the first table is read; the case and pack counts below it are not inventory.

Sheets are found by name. When the name is unexpected or more than one sheet could be the source, this
module says so and returns a problem instead of choosing; the page lets the person pick. A hidden
duplicate beside exactly one visible sheet is skipped, with a note. Nothing here changes a number.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Optional

import openpyxl
import pandas as pd

from .intake import (
    EndingReport, PeriodWindow, failed_ending_report, prepare_period_receipts, read_ending_report,
)
from .materials import MaterialMatcher

RECEIVED_SHEETS = ("RECEIVEDLOG", "RECEIVED")
_PERIOD_END_SHEET = re.compile(r"^PERIODEND(\d{4})$")
_PERIOD_HEADER = re.compile(r"^\s*P(?:ERIOD)?\.?\s*0?(\d{1,2})\s*$", re.IGNORECASE)
_RECEIVED_COLUMNS = 13        # A:M; the sheet carries hundreds of unused columns to the right
_PERIOD_END_COLUMNS = 17      # A:Q
NONE_CHOICE = "(none)"

# header -> documented column letter, for the "columns moved" note only; the header is the authority
_RECEIVED_LAYOUT = {"ITEMS": "B", "COMPANY": "C", "DATEDELIVERED": "D", "BOL#": "F", "PO#": "H",
                    "QUANTITY": "I", "PRICE": "J", "PERIOD": "K", "INVOICE#": "L"}
_REQUIRED = ("ITEMS", "DATEDELIVERED", "QUANTITY", "PRICE")


def _key(text) -> str:
    return re.sub(r"[^A-Z0-9#]", "", str(text).upper())


def _letter(index: int) -> str:
    return openpyxl.utils.get_column_letter(index + 1)


@dataclass
class SheetPlan:
    received: Optional[str] = None
    period_end: Optional[str] = None
    problems: list = field(default_factory=list)        # block until the person chooses
    notes: list = field(default_factory=list)
    sheets: list = field(default_factory=list)          # [(name, state)]
    received_choices: list = field(default_factory=list)
    period_end_choices: list = field(default_factory=list)


def list_sheets(data: bytes) -> list[tuple[str, str]]:
    """[(sheet name, 'visible' | 'hidden' | 'veryHidden')] in workbook order."""
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        return [(ws.title, ws.sheet_state) for ws in wb.worksheets]
    finally:
        wb.close()


def _pick(candidates: list[tuple[str, str]], what: str, plan: SheetPlan) -> Optional[str]:
    """One sheet from several by-name candidates, or None with a problem recorded."""
    if not candidates:
        return None
    visible = [name for name, state in candidates if state == "visible"]
    if len(visible) == 1:
        skipped = [name for name, _ in candidates if name != visible[0]]
        if skipped:
            plan.notes.append(f"Used the visible '{visible[0]}' for the {what}; ignored hidden "
                              + ", ".join(f"'{n}'" for n in skipped) + ".")
        return visible[0]
    if not visible and len(candidates) == 1:
        plan.notes.append(f"The only {what} sheet, '{candidates[0][0]}', is hidden; it was used.")
        return candidates[0][0]
    names = [name for name, _ in (candidates if not visible else [c for c in candidates if c[0] in visible])]
    plan.problems.append(f"More than one sheet could be the {what}: {', '.join(repr(n) for n in names)}. "
                         "Choose one below.")
    return None


def plan_sheets(sheets: list[tuple[str, str]], fiscal_year: int,
                received_sheet: Optional[str] = None, period_end_sheet: Optional[str] = None) -> SheetPlan:
    plan = SheetPlan(sheets=list(sheets))
    names = [n for n, _ in sheets]

    if received_sheet == NONE_CHOICE:
        plan.notes.append("No received log was used; no receipts are added.")
    elif received_sheet:
        plan.received = received_sheet
    else:
        found = [(n, s) for n, s in sheets if _key(n) in RECEIVED_SHEETS]
        plan.received = _pick(found, "received log", plan)
        if not found:
            plan.problems.append("No sheet named 'RECEIVED LOG' was found. Sheets in this workbook: "
                                 + ", ".join(repr(n) for n in names) + ". Choose the one with the received log.")

    year = str(int(fiscal_year))
    if period_end_sheet == NONE_CHOICE:
        plan.notes.append("No period-end sheet was used; no ending quantities are applied.")
    elif period_end_sheet:
        plan.period_end = period_end_sheet
    else:
        period_end_all = [(n, s) for n, s in sheets if _key(n).startswith("PERIODEND")]
        found = [(n, s) for n, s in sheets
                 if (m := _PERIOD_END_SHEET.match(_key(n))) and m.group(1) == year]
        plan.period_end = _pick(found, f"FY{year} period-end sheet", plan)
        others = [n for n, _ in period_end_all if n not in {f[0] for f in found}]
        if found and others:
            plan.notes.append("Other period-end sheets were not used: " + ", ".join(repr(n) for n in others) + ".")
        if not found:
            plan.problems.append(
                f"No 'PERIOD END {year}' sheet was found" + (
                    f" (found: {', '.join(repr(n) for n in others)})" if others else "")
                + ". Choose the sheet with this fiscal year's ending quantities.")
    plan.received_choices = [NONE_CHOICE] + names
    plan.period_end_choices = [NONE_CHOICE] + names
    return plan


# ------------------------------------------------------------------
# RECEIVED LOG
# ------------------------------------------------------------------

def _read_raw(data: bytes, sheet: str, columns: int) -> pd.DataFrame:
    """The sheet's first ``columns`` columns as a header-less frame. The received log carries hundreds of
    unused columns, so only the used ones are read; a narrower sheet is simply padded."""
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        rows = [list(r) + [None] * (columns - len(r))
                for r in wb[sheet].iter_rows(max_col=columns, values_only=True)]
    finally:
        wb.close()
    return pd.DataFrame(rows, columns=range(columns), dtype=object)


def read_received_log(data: bytes, sheet: str) -> tuple[Optional[pd.DataFrame], list, list]:
    """(table, structural errors, notes). The table's columns are the header texts; ``_SOURCE_ROW`` is the
    worksheet row. Ambiguous or missing required headers are errors, never a guess."""
    raw = _read_raw(data, sheet, _RECEIVED_COLUMNS)
    header_row = None
    for i in range(min(10, len(raw))):
        keys = [_key(v) for v in raw.iloc[i] if not pd.isna(v)]
        if "DATEDELIVERED" in keys and "QUANTITY" in keys:
            header_row = i
            break
    if header_row is None:
        return None, [f"Sheet '{sheet}' has no header row with DATE DELIVERED and QUANTITY in its first rows."], []
    headers = [None if pd.isna(v) else str(v).strip() for v in raw.iloc[header_row]]
    keys = [_key(h) if h else "" for h in headers]
    errors, notes = [], []
    for required in _REQUIRED:
        hits = [i for i, k in enumerate(keys) if k == required]
        if not hits:
            errors.append(f"Sheet '{sheet}' has no '{required}' column in columns A-M.")
        elif len(hits) > 1:
            errors.append(f"Sheet '{sheet}' has more than one '{required}' column "
                          f"({', '.join(_letter(i) for i in hits)}); it is not clear which to use.")
    if errors:
        return None, errors, notes
    moved = [f"{h} is in column {_letter(keys.index(k))}, not {letter}" for k, letter in _RECEIVED_LAYOUT.items()
             if k in keys for h in [headers[keys.index(k)]] if _letter(keys.index(k)) != letter]
    if moved:
        notes.append("The received log's columns differ from the usual layout (" + "; ".join(moved)
                     + "). Columns were found by their headers.")
    table = raw.iloc[header_row + 1:].copy()
    names, seen = [], {}
    for i, h in enumerate(headers):
        label = h if h else f"COLUMN {_letter(i)}"
        seen[label] = seen.get(label, 0) + 1
        names.append(label if seen[label] == 1 else f"{label} ({_letter(i)})")
    table.columns = names
    table["_SOURCE_ROW"] = [header_row + 2 + n for n in range(len(table))]
    table["SECTION PERIOD"] = _section_headings(table, keys)
    return table.dropna(how="all", subset=[c for c in table.columns if c != "_SOURCE_ROW"]).reset_index(drop=True), [], notes


_SECTION_HEADING = re.compile(r"^\s*PERIOD\s*\d{1,2}\s*FYE", re.IGNORECASE)


def _section_headings(table: pd.DataFrame, keys: list) -> list:
    """The merged "PERIOD 12 FYE 8/2-29/2026" heading in force for each row: a row whose first cell is such a
    heading and whose ITEMS, date, quantity and cost are blank starts a section that runs to the next heading.
    Supporting evidence for a row's period, never the filter."""
    columns = list(table.columns)
    first = columns[0]
    evidence = [columns[keys.index(k)] for k in ("ITEMS", "DATEDELIVERED", "QUANTITY", "PRICE") if k in keys]
    current, out = "", []
    for _, row in table.iterrows():
        text = row[first]
        if (isinstance(text, str) and _SECTION_HEADING.match(text)
                and all(pd.isna(row[c]) or str(row[c]).strip() == "" for c in evidence)):
            current = text.strip()
        out.append(current)
    return out


# ------------------------------------------------------------------
# PERIOD END
# ------------------------------------------------------------------

def _sheet_date(value):
    try:
        return pd.to_datetime(value).date() if not pd.isna(value) else None
    except (ValueError, TypeError):
        return None


def _calendar_findings(raw: pd.DataFrame, header_row: int, columns: dict, calendar, period: int, sheet: str):
    """Compare the sheet's period-end dates with the fiscal calendar: (errors, warnings, notes, info).

    The saved Workspace calendar is the authority; the sheet's dates never override it and a disagreement never blocks.
    A date exactly a year away from the calendar's, while its neighbours agree, is noted as a typo. A real disagreement
    about the selected period is a warning naming both dates (``info`` carries them for the page); disagreements about
    other periods are one summary warning. ``errors`` is always empty and kept for the caller's shape."""
    if header_row < 1 or calendar is None:
        return [], [], [], None
    mismatch, typo = {}, set()
    for p, cols in columns.items():
        if not 1 <= p <= len(calendar.period_ends) or len(cols) != 1:
            continue
        shown = _sheet_date(raw.iloc[header_row - 1, cols[0]])
        wanted = calendar.period_ends[p - 1]
        if shown is None or shown == wanted:
            continue
        mismatch[p] = (shown, wanted)
        if abs((shown - wanted).days) in (365, 366):
            typo.add(p)
    real = {p: v for p, v in mismatch.items() if p not in typo}
    warnings, notes, info = [], [], None
    if period in real:
        shown, wanted = real[period]
        info = {"sheet": sheet, "fiscal_year": int(calendar.year), "period": int(period), "workbook_date": shown,
                "calendar_date": wanted, "periods_differing": len(real), "periods_compared": len(columns)}
        warnings.append(
            f"'{sheet}' says Period {period} ends {shown:%b %d, %Y}, but the Workspace fiscal calendar says it ends "
            f"{wanted:%b %d, %Y} ({len(real)} of {len(columns)} periods differ). The Workspace calendar was used, so "
            "receipts were placed by its dates; the sheet's date was ignored. If the calendar is the one that is "
            "wrong, correct it on the Workspace page.")
    elif real:
        warnings.append(f"'{sheet}' and the Workspace fiscal calendar disagree about period(s) "
                        + ", ".join(str(p) for p in sorted(real)) + "; the calendar was used and the selected period is not affected.")
    if period in typo:
        shown, wanted = mismatch[period]
        notes.append(f"'{sheet}' dates Period {period} as ending {shown:%b %d, %Y}, exactly a year from the calendar's "
                     f"{wanted:%b %d, %Y}. That is a year typo in the workbook; the calendar's date was used.")
    return [], warnings, notes, info


def read_period_end(data: bytes, sheet: str, matcher: MaterialMatcher, period: int,
                    window: Optional[PeriodWindow] = None, calendar=None) -> EndingReport:
    raw = _read_raw(data, sheet, _PERIOD_END_COLUMNS)
    header_row, columns = None, {}
    for i in range(min(10, len(raw))):
        found = {}
        for j, v in enumerate(raw.iloc[i]):
            m = _PERIOD_HEADER.match(str(v)) if not pd.isna(v) else None
            if m:
                found.setdefault(int(m.group(1)), []).append(j)
        if len(found) >= 3:
            header_row, columns = i, found
            break
    if header_row is None:
        return failed_ending_report(period, f"Sheet '{sheet}' has no row of 'Period 1' ... 'Period 13' headings.")
    hits = columns.get(int(period), [])
    if not hits:
        return failed_ending_report(period, f"Sheet '{sheet}' has no 'Period {int(period)}' column.")
    if len(hits) > 1:
        return failed_ending_report(period, f"Sheet '{sheet}' has more than one 'Period {int(period)}' column "
                                            f"({', '.join(_letter(j) for j in hits)}); it is not clear which to use.")
    qty_col = hits[0]

    description_col = 2
    for i in (header_row - 1, header_row):
        if i >= 0:
            for j, v in enumerate(raw.iloc[i]):
                if not pd.isna(v) and str(v).strip().upper() == "DESCRIPTION":
                    description_col = j

    names, quantities, rows = [], [], []
    seen_data, ended_at = False, None
    for i in range(header_row + 1, len(raw)):
        row = raw.iloc[i]
        name = row.iloc[description_col]
        counts = [row.iloc[j] for js in columns.values() for j in js]
        if pd.isna(name) and all(pd.isna(c) for c in counts):
            if seen_data:
                ended_at = i + 1
                break
            continue
        seen_data = True
        names.append(None if pd.isna(name) else str(name))
        quantities.append(None if pd.isna(row.iloc[qty_col]) else row.iloc[qty_col])
        rows.append(i + 1)

    table = pd.DataFrame({"Item": names, "Ending Quantity": pd.Series(quantities, dtype=object), "_SOURCE_ROW": rows})
    report = read_ending_report(table, matcher, period, layout_label=f"sheet '{sheet}', Period {int(period)} column "
                                                                     f"{_letter(qty_col)}")
    if ended_at is not None:
        report.notes.append(f"Only the first table of '{sheet}' was read (it ends at row {ended_at - 1}); the "
                            "tables below it are not inventory.")
    cal_errors, cal_warnings, cal_notes, cal_info = _calendar_findings(raw, header_row, columns, calendar, int(period), sheet)
    report.calendar_mismatch = cal_info
    report.structural += cal_errors
    report.errors += cal_errors
    report.warnings += cal_warnings
    report.notes += cal_notes
    stamped = raw.iloc[header_row - 1, qty_col] if header_row >= 1 else None
    if calendar is None and window is not None and window.source == "calendar" and stamped is not None and not pd.isna(stamped):
        try:
            sheet_end = pd.to_datetime(stamped).date()
            if sheet_end != window.end:
                report.warnings.append(
                    f"'{sheet}' dates Period {int(period)} as ending {sheet_end:%b %d, %Y}, but the fiscal calendar "
                    f"says {window.end:%b %d, %Y}.")
        except (ValueError, TypeError):
            pass
    return report


# ------------------------------------------------------------------
# One workbook, both sources
# ------------------------------------------------------------------

@dataclass
class OperationsImport:
    plan: SheetPlan
    ending: Optional[EndingReport] = None
    receipts: pd.DataFrame = field(default_factory=pd.DataFrame)
    receipt_errors: list = field(default_factory=list)
    receipt_warnings: list = field(default_factory=list)
    receipt_debug: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    problems: list = field(default_factory=list)       # sheet-level: choose a sheet / unreadable layout
    calendar_problem: Optional[str] = None             # the Workspace calendar is missing/invalid: nothing was read

    @property
    def has_receipts_source(self) -> bool:
        return self.plan.received is not None


def read_operations_workbook(data: bytes, fiscal_year: int, period: int, matcher: MaterialMatcher,
                             window: PeriodWindow, known_receipts: Optional[dict] = None,
                             cutoff=None, cutoff_period=None, cutoff_layers=None, decisions=None,
                             received_sheet: Optional[str] = None,
                             period_end_sheet: Optional[str] = None, calendar=None,
                             require_calendar: bool = False) -> OperationsImport:
    """Both sources from one workbook. With ``require_calendar`` (the page), ``calendar`` must be the verified Workspace
    calendar for ``fiscal_year``: when it is missing, for another year or inconsistent, nothing is read and
    ``calendar_problem`` says why (processing is blocked, never defaulted)."""
    plan = plan_sheets(list_sheets(data), fiscal_year, received_sheet, period_end_sheet)
    result = OperationsImport(plan=plan, notes=list(plan.notes), problems=list(plan.problems))
    if require_calendar:
        from .intake import calendar_problem
        result.calendar_problem = calendar_problem(calendar, fiscal_year)
        if result.calendar_problem:
            result.problems.append(result.calendar_problem)
            return result

    if plan.received:
        table, errors, notes = read_received_log(data, plan.received)
        result.notes += notes
        if errors:
            result.receipt_errors = errors
            result.receipt_debug = {'structural': list(errors)}
        else:
            (result.receipts, result.receipt_errors, result.receipt_warnings,
             result.receipt_debug) = prepare_period_receipts(
                table, matcher, window, period, material_columns=["ITEMS"], fiscal_year=fiscal_year,
                known_receipts=known_receipts, cutoff=cutoff, cutoff_period=cutoff_period,
                cutoff_layers=cutoff_layers, decisions=decisions)
            result.notes += result.receipt_debug.get('notes', [])
    if plan.period_end:
        from .intake import calendar_applies
        result.ending = read_period_end(
            data, plan.period_end, matcher, period, window,
            calendar=calendar if calendar_applies(calendar, fiscal_year) else None)
        result.notes += result.ending.notes
    return result
