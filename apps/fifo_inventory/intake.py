"""Turning operations' two period reports into what the FIFO engine takes.

* ``read_ending_report``: the period-ending inventory report -> one authoritative ending
  quantity per material. Materials are found by name or alias (``materials.py``). The report may
  be a simple list (material, quantity) or the older wide Master Grid; in the wide form only the
  selected period's column is read, so a prior-period column is ignored. The ledger is continuous,
  not a census: a material with no count (absent or blank) carries its layers forward with zero
  usage and raises nothing; an explicit 0 is a count and depletes. The beginning balance is
  never read from a file: it is whatever the last closed period left in the FIFO layers.
* ``prepare_period_receipts``: the receiving report -> the receipts that belong to the selected
  period, assigned by *delivery date* against the fiscal calendar, with every rejected row
  carrying its reason.
* ``input_exceptions`` / ``product_exception_rows``: only what genuinely needs a person.

Nothing here changes a number the engine calculates. It decides which rows reach the engine and
says why a row did not. Pure: no Streamlit. Bump ``INPUT_PROCESSING_VERSION`` when a change here
can alter which rows or quantities reach the engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import json
import re

import pandas as pd

from .excel_export import _compute_summary_control_status, _layer_period_and_dates, _quantity_is_material
from .ingestion import (
    _coerce_accounting_series, _finite_number, extract_period, find_column, parse_period_column_header,
)
from .materials import AMBIGUOUS, OK, UNMAPPED, MaterialMatcher, is_non_inventory, normalize_name
from .user_inputs import PRODUCTS, QTY_TOLERANCE, VALUE_TOLERANCE

ALIAS_COLUMNS = ['PRODUCT ALIAS', 'ALIAS', 'PRODUCT ID', 'ITEM ID']
# ITEMS comes last: in receiving reports it is often free text about the delivery, so it only
# identifies a material when nothing earlier in the row does.
NAME_COLUMNS = ['DESCRIPTION', 'PRODUCT', 'PRODUCT NAME', 'ITEM NAME', 'MATERIAL',
                'PRODUCT ID / ITEM', 'ITEM', 'ITEMS']
ENDING_QTY_COLUMNS = ['ENDING QUANTITY', 'ENDING QTY', 'END QTY', 'ENDING COUNT', 'QUANTITY', 'QTY',
                      'COUNT', 'QTY ON HAND', 'ON HAND', 'ENDING INVENTORY']
PERIOD_COLUMNS = ['FISCAL PERIOD', 'PERIOD', 'PD']

_MAX_LISTED = 25


# ------------------------------------------------------------------
# Which dates belong to the period
# ------------------------------------------------------------------

@dataclass(frozen=True)
class PeriodWindow:
    """The delivery dates that belong to the selected period. ``start`` is None when unknown."""
    end: date
    start: Optional[date] = None
    year_start: Optional[date] = None    # first day of the fiscal year, when a calendar says
    year_end: Optional[date] = None      # last day of the fiscal year, when a calendar says
    source: str = "none"                  # 'calendar', 'last_close' or 'none'

    def describe(self) -> str:
        if self.source == "calendar":
            return f"{self.start:%b %d, %Y} to {self.end:%b %d, %Y} (from the fiscal calendar)"
        if self.source == "last_close":
            return f"{self.start:%b %d, %Y} to {self.end:%b %d, %Y} (the day after the last closed period)"
        return (f"up to {self.end:%b %d, %Y}; no fiscal calendar covers this year, so receipts dated "
                "before the period are not set aside")


def calendar_applies(calendar, fiscal_year: int) -> bool:
    from shared.fiscal_calendar import validate
    return calendar is not None and int(calendar.year) == int(fiscal_year) and validate(calendar) is None


def period_window(fiscal_year: int, period: int, calendar, period_end: date,
                  last_close_end: Optional[date] = None) -> PeriodWindow:
    """The calendar's dates for the period if it covers this fiscal year; otherwise the days after the
    last closed period up to ``period_end``; otherwise everything up to ``period_end``."""
    if calendar_applies(calendar, fiscal_year):
        start = calendar.start if int(period) == 1 else calendar.period_ends[int(period) - 2] + timedelta(days=1)
        return PeriodWindow(end=calendar.period_ends[int(period) - 1], start=start,
                            year_start=calendar.start, year_end=calendar.period_ends[-1], source="calendar")
    if last_close_end is not None and last_close_end < period_end:
        return PeriodWindow(end=period_end, start=last_close_end + timedelta(days=1), source="last_close")
    return PeriodWindow(end=period_end)


# ------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------

def _find_flexible(df: pd.DataFrame, candidates) -> Optional[str]:
    """Exact normalized header first, then a header that merely starts with a candidate
    ('Fiscal Period (1-13)' for 'FISCAL PERIOD'). Candidates under four characters match exactly only."""
    import re
    exact = find_column(df, candidates)
    if exact:
        return exact
    norm = {re.sub(r'[^A-Z0-9]', '', str(c).upper()): c for c in df.columns}
    for cand in candidates:
        key = re.sub(r'[^A-Z0-9]', '', cand.upper())
        if len(key) < 4:
            continue
        for column_key, column in norm.items():
            if column_key.startswith(key):
                return column
    return None


def _present_columns(df: pd.DataFrame, candidates) -> list[str]:
    found = []
    for cand in candidates:
        column = find_column(df, [cand])
        if column is not None and column not in found:
            found.append(column)
    return found


def _is_blank(value) -> bool:
    return value is None or (not isinstance(value, str) and pd.isna(value)) or str(value).strip() == ""


def _cap(rows: list, limit: int = _MAX_LISTED) -> list:
    return rows[:limit]


def _row_label(raw_row: pd.Series, id_columns: list[str]) -> str:
    for column in id_columns:
        value = raw_row.get(column)
        if not _is_blank(value):
            return str(value).strip()
    return "(blank)"


# ------------------------------------------------------------------
# Ending inventory report
# ------------------------------------------------------------------

@dataclass
class EndingReport:
    grid: pd.DataFrame                    # PRODUCT ALIAS, DESCRIPTION, <period> for every material
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    structural: list = field(default_factory=list)    # the file itself cannot be read as a report
    missing: list = field(default_factory=list)       # [(alias, name)] with no count (carried forward)
    duplicates: dict = field(default_factory=dict)    # alias -> [source rows]
    invalid: list = field(default_factory=list)       # [{'row', 'name', 'value', 'problem'}]
    unmapped: list = field(default_factory=list)      # [{'row', 'name', 'quantity', 'problem', 'suggestion'}]
    matched: int = 0                                  # materials with a supplied count
    notes: list = field(default_factory=list)         # facts worth a line, never an exception
    ignored: list = field(default_factory=list)       # non-inventory lines skipped
    layout: str = ""
    detected: dict = field(default_factory=dict)


def _empty_grid(period: int) -> pd.DataFrame:
    return pd.DataFrame({
        'PRODUCT ALIAS': list(PRODUCTS), 'DESCRIPTION': list(PRODUCTS.values()),
        f"{int(period):02d}": [float('nan')] * len(PRODUCTS),
    })


def empty_ending_grid(period: int) -> pd.DataFrame:
    """No counts at all: every material carries forward."""
    return _empty_grid(period)


def failed_ending_report(period: int, message: str) -> EndingReport:
    """An ending report that could not be read at all, so the page can say why and stop."""
    return EndingReport(grid=_empty_grid(period), errors=[message], structural=[message])


def read_ending_report(df_upload: pd.DataFrame, matcher: MaterialMatcher, period: int,
                       layout_label: Optional[str] = None) -> EndingReport:
    period = int(period)
    end_col = f"{period:02d}"
    report = EndingReport(grid=_empty_grid(period))
    df = df_upload.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if '_SOURCE_ROW' not in df.columns:
        df['_SOURCE_ROW'] = range(2, len(df) + 2)

    wide = [c for c in df.columns if c != '_SOURCE_ROW' and parse_period_column_header(c) == (period, 'qty')]
    period_col = None
    if wide:
        qty_col = wide[0]
        report.layout = f"period columns (column {qty_col})"
    else:
        qty_col = _find_flexible(df, ENDING_QTY_COLUMNS)
        period_col = _find_flexible(df, PERIOD_COLUMNS)
        report.layout = f"one row per material (column {qty_col})" if qty_col else ""
    if layout_label and qty_col is not None:
        report.layout = layout_label
    if qty_col is None:
        report.structural.append(
            f"The ending inventory report has no quantity column for period {period:02d}. Expected a column "
            "such as 'Ending Quantity' (one row per material) or a period column named "
            f"'{end_col}' / 'P{period}'.")
        report.errors += report.structural
        return report

    id_columns = _present_columns(df, ALIAS_COLUMNS) + [c for c in _present_columns(df, NAME_COLUMNS)
                                                         if c not in _present_columns(df, ALIAS_COLUMNS)]
    id_columns = [c for c in id_columns if c != qty_col]
    if not id_columns:
        first = next((c for c in df.columns if c not in ('_SOURCE_ROW', qty_col)), None)
        if first is None:
            report.structural.append("The ending inventory report has no column that names the material.")
            report.errors += report.structural
            return report
        id_columns = [first]
    report.detected = {'identifying columns': id_columns, 'quantity column': qty_col,
                       'period column': period_col}

    if period_col is not None:
        parsed = df[period_col].apply(extract_period)
        unreadable = int((parsed.isna() & df[period_col].notna()).sum())
        df = df[parsed == period].copy()
        if unreadable:
            report.warnings.append(f"{unreadable} row(s) have a period label that could not be read and were not used.")
        if df.empty:
            report.structural.append(f"The ending inventory report has no rows for period {period:02d}.")
            report.errors += report.structural
            return report

    by_alias: dict[int, list] = {}
    invalid_aliases: set = set()
    for _, row in df.iterrows():
        raw_qty = row[qty_col]
        ids = [row[c] for c in id_columns]
        if all(_is_blank(v) for v in ids) and _is_blank(raw_qty):
            continue                                         # an empty row
        match = matcher.match_first(ids)
        name = _row_label(row, id_columns)
        source_row = int(row['_SOURCE_ROW'])

        if match.status != OK:
            if _is_blank(raw_qty):
                continue                                     # a heading such as "LABELS", not a count
            if match.status == UNMAPPED and is_non_inventory(name):
                report.ignored.append(name)                  # pallet wrap, slip sheets: not a FIFO material
                continue
            report.unmapped.append({
                'row': source_row, 'name': name, 'quantity': raw_qty,
                'problem': match.detail if match.status == AMBIGUOUS else "not a known material",
                'suggestion': "; ".join(matcher.suggestions(name)),
            })
            continue

        if _is_blank(raw_qty):
            continue                                         # no count supplied: this material carries forward
        try:
            value = _finite_number(raw_qty, "quantity")
            if value < -QTY_TOLERANCE:
                raise ValueError("quantity is negative")
        except ValueError as exc:
            report.invalid.append({'row': source_row, 'name': name, 'value': raw_qty, 'problem': str(exc)})
            invalid_aliases.add(match.alias)
            continue
        by_alias.setdefault(match.alias, []).append((source_row, value, normalize_name(name), name))

    grid = _empty_grid(period)
    for alias, entries in by_alias.items():
        if len(entries) > 1:
            if len({e[2] for e in entries}) == len(entries):
                # Different spellings the books map to one material (Natural Caps and CSI Caps): their
                # counts are that material's count together. Said out loud, never silent.
                total = sum(e[1] for e in entries)
                grid.loc[grid['PRODUCT ALIAS'] == alias, end_col] = total
                report.notes.append(
                    f"{PRODUCTS[alias]}: " + " + ".join(f"{e[3].strip()} {e[1]:,.0f}" for e in entries)
                    + f" = {total:,.0f} counted together.")
            else:
                report.duplicates[alias] = [e[0] for e in entries]    # the same line listed twice
            continue
        grid.loc[grid['PRODUCT ALIAS'] == alias, end_col] = entries[0][1]
    report.grid = grid
    report.matched = len(by_alias) - len(report.duplicates)
    if report.ignored:
        report.notes.append(f"{len(report.ignored)} non-inventory supply line(s) were ignored.")

    for alias, name in PRODUCTS.items():
        if alias not in by_alias and alias not in invalid_aliases and alias not in report.duplicates:
            report.missing.append((alias, name))     # no count: carried forward, not an exception

    if report.duplicates:
        listed = ", ".join(f"{PRODUCTS[a]} (rows {', '.join(map(str, r))})" for a, r in report.duplicates.items())
        report.errors.append(
            f"These materials are counted more than once, so the count is ambiguous and none was used: {listed}.")
    if report.invalid:
        report.errors.append(
            f"{len(report.invalid)} ending quantity cell(s) are negative or not a number: "
            + ", ".join(f"{i['name']} (row {i['row']})" for i in _cap(report.invalid, 10)))
    if report.unmapped:
        report.warnings.append(
            f"{len(report.unmapped)} line(s) in the ending report were not recognized as a material and were "
            "not used: " + ", ".join(f"'{u['name']}'" for u in _cap(report.unmapped, 8)) + ".")
    return report


# ------------------------------------------------------------------
# Receiving report
# ------------------------------------------------------------------

def _empty_receipts_debug(**extra) -> dict:
    base = {'qty_col': None, 'price_col': None, 'date_col': None, 'id_columns': [], 'period_col': None,
            'used_position_fallback': [], 'rejected_rows': pd.DataFrame(), 'duplicate_rows': pd.DataFrame(),
            'notes': [], 'unmapped': [], 'other_period_rows': 0, 'structural': [],
            'unplaced_rows': pd.DataFrame(), 'mislabelled_rows': pd.DataFrame(), 'late_rows': pd.DataFrame(),
            'review_rows': pd.DataFrame(), 'decided_rows': pd.DataFrame(), 'opening_rows': pd.DataFrame(),
            'presumed_count': 0}
    base.update(extra)
    return base


def _label_year(text) -> Optional[int]:
    """The fiscal year a period label or heading names: 'P.12 FYE 2026', 'PERIOD 13 FYE 8/29/26-pm-10/3/2026'."""
    text = str(text)
    four = re.findall(r'(?<!\d)(20\d{2})(?!\d)', text)
    if four:
        return int(four[-1])
    two = re.findall(r'/(\d{2})(?!\d)', text)
    return 2000 + int(two[-1]) if two and 'FYE' in text.upper() else None


def _layer_lineage(layer: dict) -> list[dict]:
    raw = layer.get('source_receipts')
    try:
        entries = json.loads(raw) if isinstance(raw, str) and raw else (raw or [])
    except ValueError:
        return []
    return [e for e in entries if isinstance(e, dict)]


def opening_layer_evidence(alias: int, delivered: date, qty: float, total: float, layers_by_alias: dict,
                           po=None, invoice=None) -> tuple[str, str]:
    """Whether the opening layers show a receipt was already included: ('included' | 'not_included' | 'unclear', why).

    Included only on proof: a layer's own recorded receipts (the lineage this app writes into every layer it builds)
    name this receipt by date, quantity and PO or invoice. A layer that merely shares its date, quantity or unit cost
    proves nothing (two trucks can deliver the same load at the same price on one day), so that is *unclear*, as is a
    consolidated layer spanning the date or a layer with no readable date (it could hold anything). Otherwise nothing
    in the opening layers could be this receipt.
    """
    unit = total / qty if qty else None
    want_po = normalize_name(po) if not _is_blank(po) else ""
    want_invoice = normalize_name(invoice) if not _is_blank(invoice) else ""
    unclear: list[str] = []
    for layer in layers_by_alias.get(int(alias), []):
        for entry in _layer_lineage(layer):
            same_po = want_po and normalize_name(entry.get('po') or "") == want_po
            same_invoice = want_invoice and normalize_name(entry.get('invoice') or "") == want_invoice
            try:
                same_day = pd.to_datetime(entry.get('date')).date() == delivered
                same_qty = abs(float(entry.get('qty')) - qty) <= 0.5
            except (TypeError, ValueError):
                continue
            if same_day and same_qty and (same_po or same_invoice):
                return 'included', f"an opening layer records this receipt (PO {entry.get('po')!s}, invoice {entry.get('invoice')!s})"
        _period, start, end = _layer_period_and_dates(layer)
        layer_qty = float(layer.get('qty') or 0.0)
        layer_unit = layer.get('unit_cost')
        if layer_unit is None and layer_qty:
            layer_unit = float(layer.get('total_value') or 0.0) / layer_qty
        if start is None:
            unclear.append("an opening layer has no readable date")
        elif start == end == delivered:
            close_cost = unit and layer_unit and abs(layer_unit - unit) <= 0.005 * unit
            if close_cost or abs(layer_qty - qty) <= 0.5:
                unclear.append(f"an opening layer dated {delivered} shares its date and cost or quantity, but nothing "
                               "records that it is this receipt")
            else:
                unclear.append(f"an opening layer dated {delivered} has a different cost and quantity")
        elif start <= delivered <= end:
            unclear.append(f"an opening layer spans {start} to {end}")
    if unclear:
        return 'unclear', "; ".join(dict.fromkeys(unclear))
    return 'not_included', ""


def register_entries(receipts: pd.DataFrame, period_key: str, status: str = 'recognized') -> list[dict]:
    """The durable register rows for the receipts a period recognizes: the receipt exactly as reported
    (material and vendor as written, delivery date, reported period) and where it was recognized."""
    entries = []
    if receipts is None or receipts.empty:
        return entries
    for _, r in receipts.iterrows():
        date_text = r.get('ORIGINAL DATE DELIVERED', r['DATE DELIVERED'])
        entries.append({
            'alias': int(r['PRODUCT ALIAS']),
            'delivered': str(pd.to_datetime(date_text).date()),
            'qty': float(r['QUANTITY']), 'total': float(r['PRICE']),
            'po': '' if _is_blank(r.get('PO #')) else str(r.get('PO #')).strip(),
            'invoice': '' if _is_blank(r.get('INVOICE #')) else str(r.get('INVOICE #')).strip(),
            'item': '' if _is_blank(r.get('ITEMS')) else str(r.get('ITEMS')).strip(),
            'vendor': str(r.get('VENDOR', '') or ''),
            'reported_period': str(r.get('REPORTED PERIOD', '') or ''),
            'source_row': int(r['_SOURCE_ROW']) if '_SOURCE_ROW' in r.index and not pd.isna(r['_SOURCE_ROW']) else None,
            'recognized_in': period_key if status == 'recognized' else 'opening layers',
            'status': status,
            'late': bool(r.get('RECEIPT STATUS') == 'late'),
        })
    return entries


def receipt_register_counts(entries: list[dict]) -> dict:
    """{receipt key: occurrences} for the register, ready for ``prepare_period_receipts``."""
    counts: dict = {}
    for e in entries:
        key = _receipt_key(e['alias'], e['delivered'], e['qty'], e['total'], e.get('po'), e.get('invoice'))
        counts[key] = counts.get(key, 0) + 1
    return counts


def clamp_late_receipts(receipts: pd.DataFrame, layers_by_alias: dict) -> pd.DataFrame:
    """Receipts as the engine takes them. A late receipt is recognized now, so it joins the FIFO queue after
    the layers already carried; when its delivery date is older than the newest of those layers, the engine's
    chronology rule would refuse it, so its layer date is the newest carried layer's date instead. The
    delivery date as reported is kept in ``ORIGINAL DATE DELIVERED`` and in the register."""
    if receipts is None or receipts.empty:
        return receipts
    out = receipts.copy()
    out['ORIGINAL DATE DELIVERED'] = out['DATE DELIVERED']
    from .excel_export import _parse_layer_date
    for index, row in out.iterrows():
        if row.get('RECEIPT STATUS') != 'late':
            continue
        newest = None
        for layer in layers_by_alias.get(int(row['PRODUCT ALIAS']), []):
            day = _parse_layer_date(layer.get('date_end') or layer.get('date'))
            if day is not None and (newest is None or day > newest):
                newest = day
        if newest is not None and pd.Timestamp(row['DATE DELIVERED']).date() < newest:
            out.at[index, 'DATE DELIVERED'] = pd.Timestamp(newest)
    return out


def _receipt_key(alias, day, qty, total, po, invoice):
    def text(v):
        return normalize_name(v) if not _is_blank(v) else ""
    try:
        return (int(alias), str(pd.to_datetime(day).date()), round(float(qty), 4), round(float(total), 2),
                text(po), text(invoice))
    except (TypeError, ValueError):
        return (int(alias), str(day), qty, total, text(po), text(invoice))


def prepare_period_receipts(df_upload: pd.DataFrame, matcher: MaterialMatcher, window: PeriodWindow,
                            period: int, material_columns: Optional[list] = None,
                            fiscal_year: Optional[int] = None, known_receipts: Optional[dict] = None,
                            cutoff: Optional[date] = None, cutoff_period: Optional[int] = None,
                            cutoff_layers: Optional[dict] = None, decisions: Optional[dict] = None):
    """``(valid, errors, warnings, debug)`` for the receipts this period recognizes.

    Every valid receipt is recognized exactly once. ``cutoff`` is the explicit historical cutoff: the opening
    FIFO layers (``cutoff_layers``) are the inventory position on that day, and ``known_receipts`` is the
    durable register of everything recognized since (``{receipt key: occurrences}``, including receipts later
    consumed in full; per occurrence, so two genuinely identical deliveries are each recognized once).

    * Dated after the cutoff, before the period, not in the register: *late*, recognized now, informational flag.
    * Dated on or before the cutoff: presumed already inside the opening layers and not imported, unless its
      period label (column K, else the merged "PERIOD n FYE" heading above it) assigns it to a period after the
      cutoff and no later than this one (the 7/31 caps receipt labelled P12). Then the opening layers are asked:
      a matching layer means included (skipped), nothing matching means recognized now, and evidence that
      cannot tell (a same-day layer at another cost, a consolidated layer spanning the date, a layer with no
      readable date, or K and the heading disagreeing) puts that one receipt up for a one-time ``decisions`` entry
      (``'include'`` or ``'opening'``), listed in ``debug['review_rows']`` and reported as an error until decided.
    * Dated after the period: pending for its own period.

    Rows with an unreadable date cannot be placed and are rejected only when their label names this period; a
    zero-cost receipt is used and noted; a blank or unreadable cost is rejected.
    """
    df = df_upload.copy()
    df.columns = [str(c).strip().upper() for c in df.columns]
    if '_SOURCE_ROW' not in df.columns:
        df['_SOURCE_ROW'] = range(2, len(df) + 2)
    data_columns = [c for c in df.columns if c != '_SOURCE_ROW']
    df = df.dropna(how='all', subset=data_columns).copy() if data_columns else df

    errors: list[str] = []
    warnings: list[str] = []
    fallback: list[str] = []

    qty_col = find_column(df, ['QUANTITY', 'QTY', 'QUANTITY RECEIVED', 'QTY RECEIVED'])
    price_col = find_column(df, ['PRICE', 'TOTAL COST', 'TOTAL PRICE', 'AMOUNT', 'COST', 'TOTAL'])
    date_col = find_column(df, ['DATE DELIVERED', 'DELIVERY DATE', 'DATE RECEIVED', 'DATE'])
    period_col = _find_flexible(df, PERIOD_COLUMNS)
    alias_cols = _present_columns(df, ALIAS_COLUMNS)
    id_columns = alias_cols + [c for c in _present_columns(df, NAME_COLUMNS) if c not in alias_cols]
    if material_columns is not None:
        # The caller knows which column names the material (operations' ITEMS column); any other column
        # that merely looks like a name, such as DESCRIPTION holding a vendor, must not decide it.
        id_columns = [str(c).strip().upper() for c in material_columns if str(c).strip().upper() in df.columns]
    if not qty_col and len(data_columns) > 9:
        qty_col = data_columns[9]
        fallback.append(f"Quantity -> column 10 ('{qty_col}')")
    if not price_col and len(data_columns) > 10:
        price_col = data_columns[10]
        fallback.append(f"Total value -> column 11 ('{price_col}')")

    missing = [label for label, col in [('Material (alias or item name)', id_columns), ('Quantity', qty_col),
                                        ('Total Value', price_col), ('Delivery Date', date_col)] if not col]
    debug = _empty_receipts_debug(qty_col=qty_col, price_col=price_col, date_col=date_col,
                                  id_columns=id_columns, period_col=period_col, used_position_fallback=fallback)
    if missing:
        errors.append("The receiving report is missing required field(s): " + ", ".join(missing))
        debug['structural'] = list(errors)
        return pd.DataFrame(), errors, warnings, debug

    vendor_col_name = find_column(df, ['COMPANY', 'VENDOR', 'SUPPLIER'])
    work = df.copy()
    work['_DATE'] = pd.to_datetime(work[date_col], errors='coerce')
    day = work['_DATE'].dt.date

    # Period evidence: column K first, the merged "PERIOD n FYE" heading above the row as support. When both
    # speak and disagree the row is a conflict, never a pick.
    section_col = 'SECTION PERIOD' if 'SECTION PERIOD' in work.columns else None
    k_text = work[period_col].map(lambda v: '' if _is_blank(v) else str(v).strip()) if period_col else pd.Series('', index=work.index)
    s_text = work[section_col].map(lambda v: '' if _is_blank(v) else str(v).strip()) if section_col else pd.Series('', index=work.index)
    k_period, s_period = k_text.map(extract_period), s_text.map(extract_period)
    use_k = k_period.notna()
    work['_LABEL'] = k_text.where(use_k, s_text)
    work['_LABEL_PERIOD'] = k_period.where(use_k, s_period)
    work['_LABEL_YEAR'] = work['_LABEL'].map(_label_year)
    work['_CONFLICT'] = k_period.notna() & s_period.notna() & (k_period != s_period)
    in_fiscal_year = pd.Series(False, index=work.index)
    if window.year_start is not None and window.year_end is not None:
        in_fiscal_year = day.map(lambda d: pd.notna(d) and window.year_start <= d <= window.year_end)
    year_named = (work['_LABEL_YEAR'] == int(fiscal_year)) if fiscal_year is not None else pd.Series(False, index=work.index)
    year_fits = year_named | (work['_LABEL_YEAR'].isna() & in_fiscal_year)
    start, end = window.start, window.end
    in_window = day.map(lambda d: pd.notna(d) and (start is None or d >= start) and d <= end)
    bad_date = work['_DATE'].isna()
    # A row with none of material, quantity, cost or date is spreadsheet debris, not a receipt.
    empty = work[[*id_columns, qty_col, price_col, date_col]].map(_is_blank).all(axis=1)
    bad_date = bad_date & ~empty
    # A date that cannot be read cannot be placed in a period. With a period column, the row is this
    # period's problem only if its label says so; otherwise it is an old row nobody can assign, which is
    # listed under the input details but is not an inventory exception.
    unplaced = work.iloc[0:0]
    if (period_col is not None or section_col is not None) and bad_date.any():
        # With a fiscal year known, only a label that names it ("P.12 FYE 2026") makes an unreadable date
        # this period's problem; an old year's "P.13" with a mangled date is history.
        points_here = (work['_LABEL_PERIOD'] == int(period)) & (year_named if fiscal_year is not None else True)
        unplaced = work[bad_date & ~points_here]
        bad_date = bad_date & points_here
    # Which log rows the register already accounts for (every readable row, in log order, so that an old
    # processed row cannot hide a new identical one).
    processed = pd.Series(False, index=work.index)
    register_left = dict(known_receipts or {})
    if register_left:
        qty_all = _coerce_accounting_series(work[qty_col])
        price_all = _coerce_accounting_series(work[price_col])
        for index in work.index[~empty & work['_DATE'].notna()]:
            if pd.isna(qty_all[index]) or pd.isna(price_all[index]):
                continue
            match = matcher.match_first([work.at[index, c] for c in id_columns])
            if match.status != OK:
                continue
            key = _receipt_key(match.alias, work.at[index, '_DATE'], qty_all[index], price_all[index],
                               work.at[index, 'PO #'] if 'PO #' in work.columns else None,
                               work.at[index, 'INVOICE #'] if 'INVOICE #' in work.columns else None)
            if register_left.get(key, 0) > 0:
                register_left[key] -= 1
                processed[index] = True

    # Classify rows dated before the period that the register has not accounted for.
    before_start = day.map(lambda d: pd.notna(d) and start is not None and d < start)
    candidate = before_start & ~processed & ~empty
    if cutoff is not None:
        after_cutoff = day.map(lambda d: pd.notna(d) and d > cutoff)
        on_or_before = day.map(lambda d: pd.notna(d) and d <= cutoff)
        if cutoff_period is not None:
            assigned_later = (work['_LABEL_PERIOD'] > int(cutoff_period)) & (work['_LABEL_PERIOD'] <= int(period)) & year_fits
        else:
            assigned_later = (work['_LABEL_PERIOD'] == int(period)) & year_fits
        crossing = candidate & on_or_before & assigned_later
        late = candidate & after_cutoff | crossing
        presumed_included = candidate & on_or_before & ~crossing
    else:
        # No cutoff known: only a label for this period can pull an earlier-dated row in.
        crossing = pd.Series(False, index=work.index)
        late = candidate & (work['_LABEL_PERIOD'] == int(period)) & year_fits
        presumed_included = pd.Series(False, index=work.index)
    in_window = in_window & ~processed
    evaluated = work[in_window | bad_date | late].copy()
    evaluated['_LATE'] = late[evaluated.index]
    evaluated['_CROSSING'] = crossing[evaluated.index]
    others = work[~(in_window | bad_date | empty | late | processed | presumed_included)
                  & ~work.index.isin(unplaced.index)]
    if len(unplaced):
        debug_unplaced = unplaced.drop(columns=['_DATE'])
    debug['presumed_count'] = int(presumed_included.sum())

    notes = []
    before = int(day[others.index].map(lambda d: start is not None and d < start).sum()) if len(others) else 0
    after = int(day[others.index].map(lambda d: d > end).sum()) if len(others) else 0
    if before:
        notes.append(f"{before} receipt row(s) dated before {start:%b %d, %Y} belong to earlier periods and were not used.")
    if debug['presumed_count']:
        notes.append(f"{debug['presumed_count']} receipt row(s) dated on or before the receipt cutoff ({cutoff:%b %d, %Y}) are "
                     "presumed already inside the opening FIFO layers and were not imported.")
    if after:
        notes.append(f"{after} receipt row(s) dated after {end:%b %d, %Y} belong to later periods and were not used.")
    if len(others):
        # Evidence that a row belongs here although it was not used: labelled for this period and year but
        # dated after it. It stays pending; a person should look.
        here = (work.loc[others.index, '_LABEL_PERIOD'] == int(period)) & year_fits[others.index]
        if here.any():
            mislabelled = others[here]
            sample = ", ".join(f"{d:%Y-%m-%d}" for d in mislabelled['_DATE'].dropna().head(3))
            warnings.append(f"{len(mislabelled)} receipt row(s) are labelled for period {int(period)} but dated outside "
                            f"it ({sample}), so they were not used. Check the date or the label.")
            debug['mislabelled_rows'] = mislabelled.drop(columns=['_DATE'])
    if window.year_end is not None and len(others):
        past_year = int(day[others.index].map(lambda d: d > window.year_end).sum())
        if past_year:
            warnings.append(f"{past_year} receipt row(s) are dated after the fiscal year's last day "
                            f"({window.year_end:%b %d, %Y}); check their dates.")
    if processed.any():
        notes.append(f"{int(processed.sum())} receipt row(s) are already in the receipt register (recognized in a "
                     "closed period) and were not added again.")
    if len(unplaced):
        notes.append(f"{len(unplaced)} row(s) have no readable delivery date and no period label for this period, "
                     "so they cannot be placed and were not used (listed under Input details).")
        debug['unplaced_rows'] = debug_unplaced
    debug['notes'] = notes
    debug['other_period_rows'] = len(others)

    matches = evaluated.apply(lambda r: matcher.match_first([r[c] for c in id_columns]), axis=1) \
        if len(evaluated) else pd.Series([], dtype=object)
    evaluated['_ALIAS'] = [m.alias if m.status == OK else None for m in matches]
    evaluated['_MATCH'] = list(matches)
    supply = [m.status == UNMAPPED and is_non_inventory(_row_label(r, id_columns))
              for m, (_, r) in zip(matches, evaluated.iterrows())]
    if any(supply):
        notes.append(f"{sum(supply)} non-inventory supply row(s) (parts, paper, wrap and the like) were ignored.")
        evaluated = evaluated[[not x for x in supply]].copy()
    evaluated['_QTY'] = _coerce_accounting_series(evaluated[qty_col]) if len(evaluated) else pd.Series(dtype=float)
    evaluated['_PRICE'] = _coerce_accounting_series(evaluated[price_col]) if len(evaluated) else pd.Series(dtype=float)

    reasons = []
    for _, row in evaluated.iterrows():
        why = []
        match = row['_MATCH']
        if match.status != OK:
            name = _row_label(row, id_columns)
            if match.status == AMBIGUOUS:
                why.append(f"Material '{name}' is ambiguous: {match.detail}")
            else:
                hint = matcher.suggestions(name)
                why.append(f"Material '{name}' not recognized" + (f" (did you mean {hint[0]}?)" if hint else ""))
        if pd.isna(row['_QTY']):
            why.append("Quantity is missing or not a number")
        elif row['_QTY'] <= QTY_TOLERANCE:
            why.append("Quantity must be positive")
        raw_price = row[price_col]
        if pd.isna(row['_PRICE']):
            if _is_blank(raw_price):
                why.append("Cost is missing (enter 0 only when the receipt truly has no cost)")
            else:
                why.append(f"Cost is not a number: {raw_price!r}")
        elif row['_PRICE'] < -VALUE_TOLERANCE:
            why.append("Cost is negative")
        if pd.isna(row['_DATE']):
            why.append("Delivery date is missing or not a date")
        reasons.append("; ".join(why))
    evaluated['REASON'] = reasons

    rejected_mask = evaluated['REASON'] != ""
    rejected = evaluated[rejected_mask].copy()
    debug['unmapped'] = sorted({_row_label(r, id_columns) for _, r in rejected.iterrows()
                                if r['_MATCH'].status != OK})
    if not rejected.empty:
        rows = rejected['_SOURCE_ROW'].tolist()
        errors.append(
            f"{len(rejected)} receipt row(s) in this period were rejected and are not in the calculation "
            f"(source rows {', '.join(map(str, _cap(rows, 15)))}{'...' if len(rows) > 15 else ''}).")
    rejected_view = rejected[['_SOURCE_ROW', 'REASON', *id_columns[:1], date_col, qty_col, price_col]].rename(
        columns={'_SOURCE_ROW': 'SOURCE ROW'}) if not rejected.empty else pd.DataFrame()
    debug['rejected_rows'] = rejected_view

    valid = evaluated[~rejected_mask].copy()
    if not valid.empty:
        labelled = valid['_LABEL_PERIOD']
        differs = int((labelled.notna() & (labelled != int(period)) & ~valid['_LATE']).sum())
        if differs:
            warnings.append(f"{differs} receipt row(s) carry a period label that differs from the period their "
                            "delivery date falls in; the delivery date was used.")
    if fiscal_year is not None and not valid.empty:
        off_year = int((valid['_LABEL_YEAR'].notna() & (valid['_LABEL_YEAR'] != int(fiscal_year))).sum())
        if off_year:
            warnings.append(f"{off_year} receipt row(s) are labelled for a different fiscal year than FY{int(fiscal_year)}.")

    # Receipts dated before the cutoff but assigned to a later period (or whose evidence conflicts): ask the
    # opening layers, then the one-time decision, never a guess.
    review_rows, decided_rows, opening_rows = [], [], []
    drop = []
    for index, row in valid.iterrows():
        if not (row['_CROSSING'] or (row['_LATE'] and row['_CONFLICT'])):
            continue
        alias = int(row['_ALIAS'])
        key = _receipt_key(alias, row['_DATE'], row['_QTY'], row['_PRICE'],
                           row.get('PO #') if 'PO #' in row.index else None,
                           row.get('INVOICE #') if 'INVOICE #' in row.index else None)
        why = ""
        if row['_CONFLICT']:
            evidence, why = 'unclear', (f"column K says {k_text[index]!r} but the heading above it says {s_text[index]!r}")
        elif row['_CROSSING']:
            evidence, why = opening_layer_evidence(alias, row['_DATE'].date(), float(row['_QTY']), float(row['_PRICE']),
                                                   cutoff_layers if cutoff_layers is not None else {},
                                                   po=row.get('PO #') if 'PO #' in row.index else None,
                                                   invoice=row.get('INVOICE #') if 'INVOICE #' in row.index else None)
        else:
            evidence = 'not_included'
        decision = (decisions or {}).get(key)
        record = {
            'KEY': "|".join(map(str, key)), 'SOURCE ROW': row['_SOURCE_ROW'],
            'MATERIAL': _row_label(row, id_columns), 'DELIVERED': row['_DATE'].date(),
            'REPORTED PERIOD': row['_LABEL'], 'QUANTITY': row['_QTY'], 'TOTAL COST': row['_PRICE'],
            'VENDOR': '' if not vendor_col_name or _is_blank(row.get(vendor_col_name)) else str(row.get(vendor_col_name)).strip(),
            'WHY': why, '_KEY': key, '_ROW': index,
        }
        if decision == 'include':
            decided_rows.append({**record, 'DECISION': 'include'})
        elif decision == 'opening':
            decided_rows.append({**record, 'DECISION': 'opening'})
            opening_rows.append(row)
            drop.append(index)
        elif evidence == 'included':
            notes.append(f"Source row {row['_SOURCE_ROW']} ({_row_label(row, id_columns)}, delivered {row['_DATE']:%Y-%m-%d}) "
                         f"was already included in the opening layers ({why}) and was not imported again.")
            opening_rows.append(row)
            drop.append(index)
        elif evidence == 'unclear':
            review_rows.append(record)
            drop.append(index)
    valid = valid.drop(index=drop)
    debug['review_rows'] = pd.DataFrame(review_rows) if review_rows else pd.DataFrame()
    debug['decided_rows'] = pd.DataFrame(decided_rows) if decided_rows else pd.DataFrame()
    if review_rows:
        errors.append(f"{len(review_rows)} receipt(s) dated before the receipt cutoff are assigned to this period but the "
                      "opening layers cannot show whether they were already included. Confirm each one once.")
    opening_view = pd.DataFrame()
    if opening_rows:
        opening_view = pd.DataFrame([{
            'PRODUCT ALIAS': int(r['_ALIAS']), 'DATE DELIVERED': r['_DATE'], 'QUANTITY': float(r['_QTY']),
            'PRICE': float(r['_PRICE']), 'PO #': r.get('PO #', ''), 'INVOICE #': r.get('INVOICE #', ''),
            'ITEMS': _row_label(r, id_columns), 'VENDOR': '' if not vendor_col_name or _is_blank(r.get(vendor_col_name))
            else str(r.get(vendor_col_name)).strip(), 'REPORTED PERIOD': r['_LABEL'], '_SOURCE_ROW': r['_SOURCE_ROW'],
        } for r in opening_rows])
    debug['opening_rows'] = opening_view

    vendor_col = vendor_col_name
    valid['RECEIPT STATUS'] = valid['_LATE'].map(lambda late_row: 'late' if late_row else 'in period')
    valid['REPORTED PERIOD'] = valid['_LABEL']
    valid['VENDOR'] = valid[vendor_col].map(lambda v: '' if _is_blank(v) else str(v).strip()) if vendor_col else ''
    if valid['_LATE'].any():
        late_view = valid[valid['_LATE']]
        notes.append(f"{len(late_view)} late receipt(s) were never processed before and are recognized in this period "
                     "(listed under Late receipts picked up).")
        debug['late_rows'] = pd.DataFrame({
            'SOURCE ROW': late_view['_SOURCE_ROW'], 'MATERIAL': late_view[id_columns[0]] if id_columns else '',
            'DELIVERED': late_view['_DATE'].dt.date, 'REPORTED PERIOD': late_view['REPORTED PERIOD'],
            'QUANTITY': late_view['_QTY'], 'TOTAL COST': late_view['_PRICE'], 'VENDOR': late_view['VENDOR'],
        }).reset_index(drop=True)
    out = valid.drop(columns=[c for c in ('_LATE', '_CROSSING', '_CONFLICT', '_LABEL', '_LABEL_PERIOD', '_LABEL_YEAR', '_MATCH', '_DATE', '_QTY', '_PRICE', 'REASON') if c in valid.columns])
    for target, source in (('QUANTITY', qty_col), ('PRICE', price_col), ('DATE DELIVERED', date_col)):
        if source != target and target in out.columns:
            out = out.drop(columns=[target])
    out = out.rename(columns={qty_col: 'QUANTITY', price_col: 'PRICE', date_col: 'DATE DELIVERED'})
    out['QUANTITY'] = _coerce_accounting_series(out['QUANTITY']).astype(float)
    out['PRICE'] = _coerce_accounting_series(out['PRICE']).astype(float)
    out['DATE DELIVERED'] = pd.to_datetime(out['DATE DELIVERED'], errors='coerce')
    for column in out.columns:
        if out[column].dtype == object:                  # lot, BOL and seal numbers mix text and numbers
            out[column] = out[column].map(lambda v: '' if _is_blank(v) else str(v).strip())
    if 'PRODUCT ALIAS' in out.columns:
        out = out.drop(columns=['PRODUCT ALIAS'])
    if 'ITEMS' not in out.columns:
        item_text = next((c for c in ('ITEM', 'ITEM NAME', 'DESCRIPTION', 'PRODUCT') if c in out.columns), None)
        if item_text:
            out['ITEMS'] = out[item_text]       # lineage text shown on the layer
    out.insert(0, 'PRODUCT ALIAS', out.pop('_ALIAS').astype(int) if len(out) else pd.Series(dtype=int))

    duplicate_subset = ['PRODUCT ALIAS', 'DATE DELIVERED', 'QUANTITY', 'PRICE']
    duplicate_subset += [c for c in ('PO #', 'INVOICE #') if c in out.columns]
    duplicate_rows = out[out.duplicated(subset=duplicate_subset, keep=False)].copy() if len(out) else pd.DataFrame()
    debug['duplicate_rows'] = duplicate_rows
    if not duplicate_rows.empty:
        warnings.append(f"{len(duplicate_rows)} receipt row(s) share every principal field and may be duplicates.")

    if 'INVOICE #' in out.columns and len(out):
        # The same invoice twice for one material is worth a look (a re-entered delivery); one invoice
        # covering several materials is ordinary and not mentioned.
        refs = out['INVOICE #'].map(lambda v: normalize_name(v) if not _is_blank(v) else "")
        pairs = pd.DataFrame({'alias': out['PRODUCT ALIAS'], 'ref': refs})
        pairs = pairs[pairs['ref'] != ""]
        counts = pairs.value_counts()
        repeated = counts[counts > 1]
        if len(repeated):
            shown = ", ".join(f"{str(out.loc[(out['PRODUCT ALIAS'] == a) & (refs == r), 'INVOICE #'].iloc[0]).strip()} (x{n})"
                              for (a, r), n in repeated.head(5).items())
            warnings.append(f"The same invoice reference is on more than one receipt row for a material: {shown}. "
                            "Each row's cost is used as listed.")

    explicit_zero = out[(out['PRICE'].abs() < VALUE_TOLERANCE) & (out['QUANTITY'] > QTY_TOLERANCE)] if len(out) else out
    if len(explicit_zero):
        warnings.append(f"{len(explicit_zero)} receipt row(s) record a cost of $0.00 (entered as zero, not blank). "
                        "They are used at zero cost.")
    if fallback:
        warnings.append("Column-position fallback used: " + "; ".join(fallback))
    return out.reset_index(drop=True), errors, warnings, debug


# ------------------------------------------------------------------
# What needs a person
# ------------------------------------------------------------------

def input_exceptions(ending: Optional[EndingReport], receipt_debug: Optional[dict]) -> list[dict]:
    """Rows for the exceptions table: ``Severity`` (ERROR blocks, WARNING does not), ``Where``, ``Item``, ``What``."""
    rows: list[dict] = []
    if ending is not None:
        for alias, source_rows in ending.duplicates.items():
            rows.append({'Severity': 'ERROR', 'Where': 'Ending report', 'Item': PRODUCTS[alias],
                         'What': f"Counted more than once (rows {', '.join(map(str, source_rows))}); none was used."})
        for item in ending.invalid:
            rows.append({'Severity': 'ERROR', 'Where': 'Ending report', 'Item': item['name'],
                         'What': f"Row {item['row']}: {item['problem']} ({item['value']!r})."})
        for item in ending.unmapped:
            hint = f" Did you mean {item['suggestion']}?" if item['suggestion'] else ""
            rows.append({'Severity': 'WARNING', 'Where': 'Ending report', 'Item': item['name'],
                         'What': f"Row {item['row']}: {item['problem']}; not used.{hint}"})
        for message in ending.structural:
            rows.append({'Severity': 'ERROR', 'Where': 'Ending report', 'Item': '', 'What': message})
    for message in (receipt_debug or {}).get('structural', []):
        rows.append({'Severity': 'ERROR', 'Where': 'Receiving report', 'Item': '', 'What': message})
    review = (receipt_debug or {}).get('review_rows')
    if review is not None and not review.empty:
        for _, row in review.iterrows():
            rows.append({'Severity': 'ERROR', 'Where': 'Receiving report', 'Item': str(row['MATERIAL']),
                         'What': f"Row {int(row['SOURCE ROW'])}, delivered {row['DELIVERED']}: needs a one-time review "
                                 f"(was it already in the opening layers?). {row['WHY']}."})
    rejected = (receipt_debug or {}).get('rejected_rows')
    if rejected is not None and not rejected.empty:
        id_col = next((c for c in rejected.columns if c not in ('SOURCE ROW', 'REASON')), None)
        for _, row in rejected.head(_MAX_LISTED).iterrows():
            rows.append({'Severity': 'ERROR', 'Where': 'Receiving report',
                         'Item': '' if id_col is None else str(row[id_col]),
                         'What': f"Row {int(row['SOURCE ROW'])}: {row['REASON']}."})
        if len(rejected) > _MAX_LISTED:
            rows.append({'Severity': 'ERROR', 'Where': 'Receiving report', 'Item': '',
                         'What': f"...and {len(rejected) - _MAX_LISTED} more rejected row(s)."})
    return rows


def product_exception_rows(all_results: list, tolerances: Optional[dict] = None) -> list[dict]:
    """Per-material problems after calculation. Materials that pass are not listed."""
    qty_tol = (tolerances or {}).get('qty_tolerance')
    rows = []
    for res in all_results:
        verdict = _compute_summary_control_status(
            res, alias_found=res.get('alias_found', True),
            alias_ambiguous=res.get('alias_ambiguous', False), tolerances=tolerances)
        status = verdict['overall_status']
        if status.startswith('PASS'):
            continue
        m = res['metrics']
        message = verdict['exception_message']
        if _quantity_is_material(m['usage_qty'], qty_tol) and m['usage_qty'] < 0 and not res.get('processing_error'):
            shortfall = -m['usage_qty']
            basis = ("there are no beginning layers on file for it" if m['beg_qty'] <= QTY_TOLERANCE
                     else f"beginning is {m['beg_qty']:,.0f}")
            message = (f"The ending count is {shortfall:,.0f} more than beginning plus receipts "
                       f"({basis}; receipts {m['purch_qty']:,.0f}), so usage would be negative.")
        rows.append({'Severity': 'ERROR' if status.startswith('FAIL') else 'WARNING', 'Where': 'Calculation',
                     'Item': f"{res['alias']} {res['prod_name']}", 'What': message})
    return rows
