"""The sheets that make the FIFO workbook read like the inventory workbook it replaces.

``export_master_excel`` (excel_export.py) still builds the Executive Summary, Summary, Input Exceptions and
one sheet per material. Given a ``context`` it also adds, in front of them:

* **Checks**: what to look at first. Overall status, the tie-outs, and only the genuine exceptions.
* **Period End Summary**: every material by fiscal period (ending, usage, purchases, unit cost on hand),
  from the closed periods' saved figures plus this period, with category totals.
* **Receipts**: every receipt recognized this period, as reported, with late receipts marked.

Nothing here calculates inventory. It lays out figures the engine and the register already hold.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

from .excel_export import (
    ALIGN_LEFT, ALIGN_RIGHT, BORDER_THIN, FILL_ALT_ROW, FILL_AMBER, FILL_GREEN, FILL_HEADER, FILL_NAVY,
    FILL_RED, FILL_SLATE, FMT_QTY, FMT_RATE, FMT_USD, FMT_USD_PAREN, FONT_AMBER, FONT_COLHDR, FONT_GREEN,
    FONT_LABEL, FONT_NORMAL, FONT_RED, FONT_SECTION, FONT_TITLE, FONT_TOTAL, HEADER_ROW_HEIGHT, ROW_HEIGHT,
    _status_style, _write_row,
)
from .user_inputs import ENGINE_VERSION, INPUT_PROCESSING_VERSION, PRODUCTS

PERIODS = range(1, 14)

CATEGORIES = (
    ("Preforms", (2,)), ("Caps", (3, 4)), ("Labels", tuple(range(5, 14))),
    ("Film", tuple(range(14, 30))), ("Glue", (30,)),
)


def _category(alias: int) -> str:
    return next((name for name, aliases in CATEGORIES if alias in aliases), "Other")


def _title(ws, text: str, width: int) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=width)
    cell = ws.cell(row=1, column=1, value=text)
    cell.fill, cell.font, cell.alignment = FILL_NAVY, FONT_TITLE, ALIGN_LEFT
    ws.row_dimensions[1].height = 24


def _section(ws, row: int, text: str, width: int) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=width)
    cell = ws.cell(row=row, column=1, value=text)
    cell.fill, cell.font, cell.alignment = FILL_SLATE, FONT_SECTION, ALIGN_LEFT
    return row + 1


# ------------------------------------------------------------------
# Checks
# ------------------------------------------------------------------

def build_check_rows(all_results: list, context: dict) -> list[tuple]:
    """(check, count or amount, status, what it means). Status is PASS, INFO, REVIEW or FAIL."""
    counts = context.get('control_counts') or {}
    receipts = context.get('receipts')
    receipts = receipts if isinstance(receipts, pd.DataFrame) else pd.DataFrame()
    late = int((receipts['RECEIPT STATUS'] == 'late').sum()) if 'RECEIPT STATUS' in receipts.columns else 0
    debug = context.get('receipt_debug') or {}
    carried = sum(1 for r in all_results if r.get('ending_carried_forward'))
    rows = []

    rows.append(("Materials calculated", len(all_results), "PASS" if all_results else "FAIL",
                 "Every material is carried forward from its saved layers; none is skipped."))
    fail, review = counts.get('FAIL', 0), counts.get('REVIEW', 0)
    rows.append(("Materials failing controls", fail, "FAIL" if fail else "PASS",
                 "See the Exceptions list below and the Executive Summary." if fail else "None."))
    rows.append(("Materials to review", review, "REVIEW" if review else "PASS",
                 "Reconciled, but a valuation or source-data condition needs a look." if review else "None."))
    rows.append(("Ending counts supplied", context.get('ending_supplied', 0), "INFO",
                 "Usage is beginning + receipts - the count, oldest layers first."))
    rows.append(("Materials carried forward (no count)", carried, "INFO",
                 "No count was supplied, so layers are kept and usage is zero. This is normal."))

    rejected = debug.get('rejected_rows')
    n_rejected = 0 if rejected is None or getattr(rejected, 'empty', True) else len(rejected)
    rows.append(("Receipt rows rejected", n_rejected, "FAIL" if n_rejected else "PASS",
                 "Not in the calculation; each is listed with its reason below." if n_rejected else "None."))
    rows.append(("Receipts recognized this period", len(receipts), "INFO",
                 f"Total cost ${float(receipts['PRICE'].sum()) if len(receipts) else 0.0:,.2f}. See the Receipts sheet."))
    rows.append(("Late receipts picked up", late, "INFO",
                 "Dated before this period but never processed before; recognized now, original date kept." if late
                 else "None."))
    presumed = int(debug.get('presumed_count') or 0)
    rows.append(("Receipts presumed in the opening layers (before the cutoff)", presumed, "INFO",
                 "Dated on or before the receipt cutoff and not assigned to a later period; not imported again."))
    decided = debug.get('decided_rows')
    n_decided = 0 if decided is None or getattr(decided, 'empty', True) else len(decided)
    if n_decided:
        rows.append(("Receipts confirmed once by a person", n_decided, "INFO",
                     "The opening layers could not show whether they were included; the answer is recorded at close."))
    n_dupes = 0 if debug.get('duplicate_rows') is None or getattr(debug.get('duplicate_rows'), 'empty', True) \
        else len(debug['duplicate_rows'])
    rows.append(("Possible duplicate receipt rows", n_dupes, "REVIEW" if n_dupes else "PASS",
                 "Identical in every field; both were kept. Confirm they are separate deliveries." if n_dupes else "None."))
    zero = int(((receipts['PRICE'].abs() < 0.005) & (receipts['QUANTITY'] > 0)).sum()) if len(receipts) else 0
    rows.append(("Receipts recorded at $0.00", zero, "REVIEW" if zero else "PASS",
                 "Entered as zero (not blank); used at zero cost." if zero else "None."))
    unmapped = len(debug.get('unmapped') or [])
    rows.append(("Receipt materials not recognized", unmapped, "FAIL" if unmapped else "PASS",
                 "Teach the app the spelling, then recalculate." if unmapped else "None."))

    beg = sum(r['metrics']['beg_val'] for r in all_results)
    purch = sum(r['metrics']['purch_val'] for r in all_results)
    usage = sum(r['metrics']['usage_val'] for r in all_results)
    end = sum(r['metrics']['end_val'] for r in all_results)
    gap = round(beg + purch - usage - end, 2)
    rows.append(("Roll-forward ties: beginning + purchases - usage - ending", gap,
                 "PASS" if abs(gap) <= 0.01 else "FAIL", "Should be $0.00."))
    receipt_gap = round((float(receipts['PRICE'].sum()) if len(receipts) else 0.0) - purch, 2)
    rows.append(("Receipts sheet ties to purchases", receipt_gap,
                 "PASS" if abs(receipt_gap) <= 0.01 else "FAIL", "Should be $0.00."))
    return rows


def _overall(rows: list[tuple]) -> str:
    statuses = {r[2] for r in rows}
    return "FAIL" if "FAIL" in statuses else ("REVIEW" if "REVIEW" in statuses else "PASS")


def add_checks_sheet(wb, all_results: list, period: int, fiscal_year, context: dict, run_issues=None):
    ws = wb.create_sheet("Checks", 0)
    for i, w in enumerate([52, 18, 12, 90], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    year = f"FY{int(fiscal_year)} - " if fiscal_year else ""
    _title(ws, f"FIFO Workpaper Checks - {year}Period {period:02d}", 4)

    rows = build_check_rows(all_results, context)
    overall = _overall(rows)
    fill, font = _status_style(overall)
    ws.merge_cells("A3:D3")
    cell = ws.cell(row=3, column=1, value=f"Overall: {overall}   (look here first)")
    cell.fill, cell.font, cell.alignment = fill, font, ALIGN_LEFT
    ws.row_dimensions[3].height = ROW_HEIGHT + 4

    r = _section(ws, 5, "Checks", 4)
    _write_row(ws, r, ["Check", "Count / amount", "Status", "What it means"], fill=FILL_HEADER, font=FONT_COLHDR,
               aligns=[ALIGN_LEFT, ALIGN_RIGHT, ALIGN_LEFT, ALIGN_LEFT], border=True)
    ws.row_dimensions[r].height = HEADER_ROW_HEIGHT
    r += 1
    for name, value, status, meaning in rows:
        money = isinstance(value, float)
        _write_row(ws, r, [name, value, status, meaning], font=FONT_NORMAL,
                   aligns=[ALIGN_LEFT, ALIGN_RIGHT, ALIGN_LEFT, ALIGN_LEFT],
                   formats=[None, FMT_USD_PAREN if money else FMT_QTY, None, None], border=True)
        if status in ("PASS", "REVIEW", "FAIL"):
            sfill, sfont = _status_style(status)
            ws.cell(row=r, column=3).fill, ws.cell(row=r, column=3).font = sfill, sfont
        ws.row_dimensions[r].height = ROW_HEIGHT
        r += 1

    exceptions = list(context.get('exceptions') or [])
    r += 1
    r = _section(ws, r, f"Exceptions ({len(exceptions)})", 4)
    if not exceptions:
        ws.cell(row=r, column=1, value="None. Nothing needs a decision.").font = FONT_GREEN
        r += 1
    else:
        _write_row(ws, r, ["Where / material", "Severity", "", "What"], fill=FILL_HEADER, font=FONT_COLHDR,
                   aligns=[ALIGN_LEFT] * 4, border=True)
        r += 1
        for item in exceptions:
            blocking = item.get('Severity') == 'ERROR'
            label = f"{item.get('Where', '')}: {item.get('Item', '')}".strip(": ")
            _write_row(ws, r, [label, "Fix" if blocking else "Heads up", "", item.get('What', '')],
                       fill=FILL_RED if blocking else FILL_AMBER, font=FONT_RED if blocking else FONT_AMBER,
                       aligns=[ALIGN_LEFT] * 4, border=True)
            ws.cell(row=r, column=4).alignment = Alignment(horizontal='left', vertical='top', wrap_text=True)
            ws.row_dimensions[r].height = 30
            r += 1

    notes = list(context.get('notes') or [])
    if notes:
        r += 1
        r = _section(ws, r, "How the source was read", 4)
        for note in notes:
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
            ws.cell(row=r, column=1, value=note).font = FONT_NORMAL
            r += 1

    r += 1
    r = _section(ws, r, "Versions", 4)
    for label, value in (("Calculation engine", ENGINE_VERSION), ("Input processing", INPUT_PROCESSING_VERSION),
                         ("Run signature", str(context.get('run_signature', ''))[:16])):
        ws.cell(row=r, column=1, value=label).font = FONT_LABEL
        ws.cell(row=r, column=2, value=value).font = FONT_NORMAL
        r += 1
    ws.freeze_panes = "A5"
    return ws


# ------------------------------------------------------------------
# Period End Summary
# ------------------------------------------------------------------

def _figures_by_period(all_results: list, history: list, period: int, fiscal_year) -> dict:
    """{period: {alias: summary dict}} for this fiscal year: closed periods' saved figures, then this period."""
    by_period: dict = {}
    for record in history or []:
        if fiscal_year is not None and int(record.get('fiscal_year', fiscal_year)) != int(fiscal_year):
            continue
        by_period[int(record['period'])] = {int(a): s for a, s in (record.get('product_period_summary') or {}).items()}
    by_period[int(period)] = {r['alias']: r['metrics'] for r in all_results}
    return by_period


def add_period_end_summary(wb, all_results: list, period: int, fiscal_year, history: Optional[list]):
    ws = wb.create_sheet("Period End Summary", 1 if "Checks" in wb.sheetnames else 0)
    width = 3 + len(PERIODS) + 1
    ws.column_dimensions['A'].width = 8
    ws.column_dimensions['B'].width = 34
    ws.column_dimensions['C'].width = 14
    for i in range(4, width + 1):
        ws.column_dimensions[get_column_letter(i)].width = 15
    year = f"FY{int(fiscal_year)} - " if fiscal_year else ""
    _title(ws, f"Period End Summary - {year}all materials, all 13 fiscal periods", width)
    ws.cell(row=2, column=1, value="Closed periods use their saved figures; this period is calculated now. "
                                   "Periods not yet processed are blank.").font = FONT_NORMAL

    figures = _figures_by_period(all_results, history, period, fiscal_year)
    blocks = (
        ("Ending Inventory - Quantity", 'end_qty', FMT_QTY, False),
        ("Ending Inventory - Value", 'end_val', FMT_USD, True),
        ("Purchases - Value", 'purch_val', FMT_USD, True),
        ("FIFO Usage - Value", 'usage_val', FMT_USD, True),
    )
    r = 4
    for title, key, fmt, subtotal in blocks:
        r = _section(ws, r, title, width)
        headers = ["Alias", "Material", "Category"] + [f"P{p:02d}" for p in PERIODS] + (["Total"] if key in ('purch_val', 'usage_val') else [""])
        _write_row(ws, r, headers, fill=FILL_HEADER, font=FONT_COLHDR,
                   aligns=[ALIGN_RIGHT, ALIGN_LEFT, ALIGN_LEFT] + [ALIGN_RIGHT] * (len(headers) - 3), border=True)
        ws.row_dimensions[r].height = HEADER_ROW_HEIGHT
        r += 1
        category_rows: dict = {}
        first = r
        for idx, (alias, name) in enumerate(PRODUCTS.items()):
            values = [(figures.get(p, {}).get(alias, {}) or {}).get(key) for p in PERIODS]
            total = sum(v for v in values if v is not None) if key in ('purch_val', 'usage_val') else None
            _write_row(ws, r, [alias, name, _category(alias)] + values + [total], font=FONT_NORMAL,
                       fill=FILL_ALT_ROW if idx % 2 else None,
                       aligns=[ALIGN_RIGHT, ALIGN_LEFT, ALIGN_LEFT] + [ALIGN_RIGHT] * (len(PERIODS) + 1),
                       formats=[None, None, None] + [fmt] * (len(PERIODS) + 1), border=True)
            category_rows.setdefault(_category(alias), []).append(r)
            ws.row_dimensions[r].height = ROW_HEIGHT
            r += 1
        if subtotal:
            for category, _aliases in CATEGORIES:
                rows_in = category_rows.get(category)
                if not rows_in:
                    continue
                sums = []
                for col in range(4, 4 + len(PERIODS) + 1):
                    letter = get_column_letter(col)
                    sums.append(f"=SUM({letter}{rows_in[0]}:{letter}{rows_in[-1]})")
                _write_row(ws, r, ["", f"{category} total", ""] + sums if key in ('purch_val', 'usage_val')
                           else ["", f"{category} total", ""] + sums[:-1] + [None],
                           font=FONT_LABEL, fill=FILL_HEADER, aligns=[ALIGN_LEFT] * 3 + [ALIGN_RIGHT] * (len(PERIODS) + 1),
                           formats=[None, None, None] + [fmt] * (len(PERIODS) + 1), border=True)
                r += 1
            letters = [get_column_letter(c) for c in range(4, 4 + len(PERIODS) + 1)]
            totals = [f"=SUM({l}{first}:{l}{first + len(PRODUCTS) - 1})" for l in letters]
            if key not in ('purch_val', 'usage_val'):
                totals[-1] = None
            _write_row(ws, r, ["", "ALL MATERIALS", ""] + totals, font=FONT_TOTAL, fill=FILL_NAVY,
                       aligns=[ALIGN_LEFT] * 3 + [ALIGN_RIGHT] * (len(PERIODS) + 1),
                       formats=[None, None, None] + [fmt] * (len(PERIODS) + 1), border=True)
            r += 1
        r += 1

    r = _section(ws, r, "Unit Cost On Hand (ending value / ending quantity)", width)
    _write_row(ws, r, ["Alias", "Material", "Category"] + [f"P{p:02d}" for p in PERIODS] + [""], fill=FILL_HEADER,
               font=FONT_COLHDR, aligns=[ALIGN_RIGHT, ALIGN_LEFT, ALIGN_LEFT] + [ALIGN_RIGHT] * 14, border=True)
    r += 1
    for idx, (alias, name) in enumerate(PRODUCTS.items()):
        costs = []
        for p in PERIODS:
            m = figures.get(p, {}).get(alias)
            costs.append((m['end_val'] / m['end_qty']) if m and m.get('end_qty') else None)
        _write_row(ws, r, [alias, name, _category(alias)] + costs + [None], font=FONT_NORMAL,
                   fill=FILL_ALT_ROW if idx % 2 else None,
                   aligns=[ALIGN_RIGHT, ALIGN_LEFT, ALIGN_LEFT] + [ALIGN_RIGHT] * 14,
                   formats=[None, None, None] + [FMT_RATE] * 14, border=True)
        r += 1
    ws.freeze_panes = "D4"
    ws.page_setup.orientation = 'landscape'
    return ws


# ------------------------------------------------------------------
# Receipts
# ------------------------------------------------------------------

def add_receipts_sheet(wb, receipts: Optional[pd.DataFrame], period: int, fiscal_year):
    index = 2 if "Checks" in wb.sheetnames and "Period End Summary" in wb.sheetnames else len(wb.sheetnames)
    ws = wb.create_sheet("Receipts", min(index, len(wb.sheetnames)))
    headers = ["Delivered", "Recognized", "Alias", "Material", "Item as reported", "Vendor", "PO #", "Invoice #",
               "Quantity", "Total cost", "Unit cost", "Reported period", "Recognized in", "Source row"]
    widths = [12, 12, 7, 32, 24, 18, 14, 28, 14, 14, 14, 17, 14, 11]
    recognized_in = f"FY{int(fiscal_year)}-P{int(period):02d}" if fiscal_year else f"P{int(period):02d}"
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    year = f"FY{int(fiscal_year)} - " if fiscal_year else ""
    _title(ws, f"Receipts recognized - {year}Period {period:02d}", len(headers))
    ws.cell(row=2, column=1, value="Every valid receipt appears once across all periods. Delivered = the date as reported; Reported "
                                   "period = the label in the log; Recognized in = the period the books took it. Late = "
                                   "dated before this period but never processed earlier.").font = FONT_NORMAL
    _write_row(ws, 4, headers, fill=FILL_HEADER, font=FONT_COLHDR, aligns=[ALIGN_LEFT] * 4 + [ALIGN_LEFT] * 4 + [ALIGN_RIGHT] * 3 + [ALIGN_LEFT, ALIGN_LEFT, ALIGN_RIGHT],
               border=True)
    ws.row_dimensions[4].height = HEADER_ROW_HEIGHT
    r = 5
    data = receipts if isinstance(receipts, pd.DataFrame) else pd.DataFrame()
    if data.empty:
        ws.cell(row=r, column=1, value="No receipts were recognized this period.").font = FONT_NORMAL
        return ws
    ordered = data.copy()
    ordered['_DELIVERED'] = pd.to_datetime(ordered.get('ORIGINAL DATE DELIVERED', ordered['DATE DELIVERED']))
    ordered = ordered.sort_values(['PRODUCT ALIAS', '_DELIVERED'], kind='mergesort')
    first = r
    for _, row in ordered.iterrows():
        alias = int(row['PRODUCT ALIAS'])
        late = row.get('RECEIPT STATUS') == 'late'
        qty, total = float(row['QUANTITY']), float(row['PRICE'])
        values = [row['_DELIVERED'].date(), "Late" if late else "In period", alias, PRODUCTS.get(alias, ''),
                  row.get('ITEMS', ''), row.get('VENDOR', ''), row.get('PO #', ''), row.get('INVOICE #', ''),
                  qty, total, (total / qty) if qty else None, row.get('REPORTED PERIOD', ''), recognized_in,
                  row.get('_SOURCE_ROW')]
        _write_row(ws, r, values, font=FONT_AMBER if late else FONT_NORMAL, fill=FILL_AMBER if late else None,
                   aligns=[ALIGN_LEFT] * 8 + [ALIGN_RIGHT] * 3 + [ALIGN_LEFT, ALIGN_LEFT, ALIGN_RIGHT],
                   formats=['yyyy-mm-dd', None, None, None, None, None, None, None, FMT_QTY, FMT_USD, FMT_RATE, None, None, None],
                   border=True)
        ws.row_dimensions[r].height = ROW_HEIGHT
        r += 1
    _write_row(ws, r, ["TOTAL", "", "", "", "", "", "", "", f"=SUM(I{first}:I{r - 1})", f"=SUM(J{first}:J{r - 1})",
                       None, "", "", ""], font=FONT_TOTAL, fill=FILL_NAVY,
               aligns=[ALIGN_LEFT] * 8 + [ALIGN_RIGHT] * 3 + [ALIGN_LEFT, ALIGN_LEFT, ALIGN_RIGHT],
               formats=[None] * 8 + [FMT_QTY, FMT_USD, None, None, None, None], border=True)
    ws.freeze_panes = "A5"
    return ws
