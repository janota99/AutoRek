import io
import re
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter

from .user_inputs import QTY_TOLERANCE

COLOR_NAVY = "0B3350"
COLOR_SLATE = "3D6690"
COLOR_HEADER_BG = "E7EEF5"
COLOR_ALT_ROW = "F5F8FB"
COLOR_GREEN_TEXT = "1B7A43"
COLOR_GREEN_FILL = "E5F4EA"
COLOR_AMBER_TEXT = "9C5B00"
COLOR_AMBER_FILL = "FDF1DD"
COLOR_RED_TEXT = "B00020"
COLOR_RED_FILL = "FBE7E9"
COLOR_TEXT_DARK = "1A202C"

FONT_TITLE = Font(color="FFFFFF", bold=True, size=14)
FONT_TITLE_PRODUCT = Font(color="FFFFFF", bold=True, size=15)
FONT_SECTION = Font(color="FFFFFF", bold=True, size=11)
FONT_SECTION_LARGE = Font(color="FFFFFF", bold=True, size=13)
FONT_COLHDR = Font(color=COLOR_NAVY, bold=True, size=11.5)
FONT_LABEL = Font(bold=True, color=COLOR_TEXT_DARK)
FONT_TOTAL = Font(bold=True, color="FFFFFF")
FONT_NORMAL = Font(color=COLOR_TEXT_DARK)
FONT_GREEN = Font(bold=True, color=COLOR_GREEN_TEXT)
FONT_AMBER = Font(bold=True, color=COLOR_AMBER_TEXT)
FONT_RED = Font(bold=True, color=COLOR_RED_TEXT)
FONT_HYPERLINK = Font(color=COLOR_NAVY, underline="single", bold=True)

FILL_NAVY = PatternFill(start_color=COLOR_NAVY, end_color=COLOR_NAVY, fill_type="solid")
FILL_SLATE = PatternFill(start_color=COLOR_SLATE, end_color=COLOR_SLATE, fill_type="solid")
FILL_HEADER = PatternFill(start_color=COLOR_HEADER_BG, end_color=COLOR_HEADER_BG, fill_type="solid")
FILL_ALT_ROW = PatternFill(start_color=COLOR_ALT_ROW, end_color=COLOR_ALT_ROW, fill_type="solid")
FILL_GREEN = PatternFill(start_color=COLOR_GREEN_FILL, end_color=COLOR_GREEN_FILL, fill_type="solid")
FILL_AMBER = PatternFill(start_color=COLOR_AMBER_FILL, end_color=COLOR_AMBER_FILL, fill_type="solid")
FILL_RED = PatternFill(start_color=COLOR_RED_FILL, end_color=COLOR_RED_FILL, fill_type="solid")

ALIGN_LEFT = Alignment(horizontal="left", vertical="center")
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")

FMT_QTY = '#,##0'
FMT_QTY_PAREN = '#,##0;(#,##0)'
FMT_USD = '$#,##0.00'
FMT_USD_PAREN = '$#,##0.00;($#,##0.00)'
FMT_RATE = '#,##0.00000000'
ROW_HEIGHT = 16
# Column section headers carry a larger, bolder font (FONT_COLHDR) than data rows,
# and several of them sit under an Excel Table's AutoFilter dropdown button. A taller
# header row keeps the enlarged text from crowding the border or the filter arrow.
HEADER_ROW_HEIGHT = 21

THIN = Side(style='thin', color='D9E2EC')
BORDER_THIN = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# ------------------------------------------------------------------
# Permanent control rules
# ------------------------------------------------------------------
# Monetary results are reported to cents. A displayed variance from -$0.01
# through +$0.01 is accepted; a displayed variance beyond that range is
# material. Unit-cost checks are separate: a layer is unpriced only when its
# unit cost is actually zero or negative. Quantity tolerance remains a small
# computational tolerance and is not an accounting-materiality setting.

_PENNY = Decimal("0.01")


def _money_at_report_precision(value):
    return Decimal(str(float(value or 0.0))).quantize(_PENNY, rounding=ROUND_HALF_UP)


def _value_is_material(value, value_tolerance=None):
    tolerance = Decimal(str(value_tolerance)) if value_tolerance is not None else _PENNY
    return abs(_money_at_report_precision(value)) > tolerance


def _value_is_acceptable(value, value_tolerance=None):
    return not _value_is_material(value, value_tolerance)


def _quantity_is_material(value, qty_tolerance=None):
    tolerance = qty_tolerance if qty_tolerance is not None else QTY_TOLERANCE
    return abs(float(value or 0.0)) > tolerance


def _layer_is_unpriced(layer, zero_cost_tolerance=0.0):
    return float(layer.get('unit_cost') or 0.0) <= zero_cost_tolerance


_LAYER_DATE_TOKEN = re.compile(r'(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{1,2}-\d{1,2})')
_LAYER_PERIOD_PREFIX = re.compile(r'^\s*(P\.?D?\.?\s*0?\d{1,2}(?:[-.]\d{2,4})?)\s*:?', re.IGNORECASE)


def parse_layer_date(date_str):
    """Public wrapper around the layer-date parser so app.py's aging report
    and product lookup views can compute layer age the same way this module
    does when building the Excel export, instead of re-implementing date
    parsing separately."""
    return _parse_layer_date(date_str)


def _parse_layer_date(date_str):
    try:
        parsed = pd.to_datetime(date_str, errors='raise')
        return parsed.date()
    except Exception:
        token_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{1,2}-\d{1,2})', str(date_str))
        if token_match:
            try:
                return pd.to_datetime(token_match.group(1), errors='raise').date()
            except Exception:
                pass
        return None

def _parse_layer_period_and_range(date_str):
    s = str(date_str).strip()
    m = _LAYER_PERIOD_PREFIX.match(s)
    source_period = m.group(1).upper().replace(' ', '') if m else ""
    rest = s[m.end():].strip() if m else s
    dates_found = _LAYER_DATE_TOKEN.findall(rest) or _LAYER_DATE_TOKEN.findall(s)
    start_date = _parse_layer_date(dates_found[0]) if dates_found else None
    end_date = _parse_layer_date(dates_found[1]) if len(dates_found) > 1 else start_date

    if not source_period and start_date is None:
        year_match = re.search(r'\b(19|20)\d{2}\b', s)
        if year_match:
            source_period = year_match.group(0)
        elif 'legacy' in s.lower() or 'carryover' in s.lower() or 'unknown' in s.lower():
            source_period = "Legacy"
    return source_period, start_date, end_date


def _layer_period_and_dates(layer):
    """Read both legacy date labels and the newer consolidated-layer fields."""
    parsed_period, parsed_start, parsed_end = _parse_layer_period_and_range(
        layer.get('date_range') or layer.get('date', 'Unknown')
    )
    start_date = _parse_layer_date(layer.get('date')) or parsed_start
    end_date = _parse_layer_date(layer.get('date_end')) or parsed_end or start_date
    source_period = layer.get('source_period') or parsed_period
    return source_period, start_date, end_date

def _check_chronology(layers):
    last_date = None
    for l in layers:
        d = _parse_layer_date(l['date'])
        if d is None:
            continue
        if last_date is not None and d < last_date:
            return False
        last_date = d
    return True

def _sanitize_sheet_name(name):
    cleaned = re.sub(r'[\[\]:*?/\\]', '', name)
    return cleaned[:31] if cleaned else "Sheet"

def _product_sheet_name(alias, prod_name):
    prefix = f"A{alias:02d} "
    core = prod_name.split(' - ')[0].strip() if ' - ' in prod_name else prod_name.strip()
    max_core_len = 31 - len(prefix)
    return _sanitize_sheet_name(prefix + core[:max_core_len])

def _link_cell_to_product_sheet(cell, alias, prod_name):
    target_sheet = _product_sheet_name(alias, prod_name)
    cell.hyperlink = f"#'{target_sheet}'!A1"
    cell.font = FONT_HYPERLINK

def _write_row(ws, row, values, col_start=1, fill=None, font=None, aligns=None, formats=None, border=False):
    for i, val in enumerate(values):
        c = ws.cell(row=row, column=col_start + i, value=val)
        if fill: c.fill = fill
        if font: c.font = font
        if aligns: c.alignment = aligns[i] if i < len(aligns) else ALIGN_LEFT
        if formats and formats[i]: c.number_format = formats[i]
        if border: c.border = BORDER_THIN
    return row

def _write_table_section(ws, start_row, title, headers, rows, col_formats, col_aligns,
                         table_name, total_row_builder=None, outline_detail=True):
    ncols = len(headers)
    ws.merge_cells(start_row=start_row, start_column=1, end_row=start_row, end_column=ncols)
    hdr_cell = ws.cell(row=start_row, column=1, value=title)
    hdr_cell.fill, hdr_cell.font, hdr_cell.alignment = FILL_SLATE, FONT_SECTION_LARGE, ALIGN_LEFT
    ws.row_dimensions[start_row].height = 22
    r = start_row + 1

    header_row = r
    _write_row(ws, r, headers, fill=FILL_HEADER, font=FONT_COLHDR, aligns=col_aligns, border=True)
    ws.row_dimensions[r].height = HEADER_ROW_HEIGHT
    r += 1

    first_data_row = r
    if not rows:
        _write_row(ws, r, ["(none)"] + [""] * (ncols - 1), font=FONT_NORMAL, aligns=col_aligns, border=True)
        ws.row_dimensions[r].height = ROW_HEIGHT
        r += 1
    else:
        for idx, row_vals in enumerate(rows):
            fill = FILL_ALT_ROW if idx % 2 == 1 else None
            _write_row(ws, r, row_vals, fill=fill, font=FONT_NORMAL,
                       aligns=col_aligns, formats=col_formats, border=True)
            ws.row_dimensions[r].height = ROW_HEIGHT
            if outline_detail:
                ws.row_dimensions[r].outlineLevel = 1
            r += 1
    last_data_row = r - 1

    if rows:
        ref = f"{get_column_letter(1)}{header_row}:{get_column_letter(ncols)}{last_data_row}"
        tbl = Table(displayName=table_name, ref=ref)
        tbl.tableStyleInfo = TableStyleInfo(name="TableStyleMedium9", showRowStripes=False,
                                             showColumnStripes=False, showFirstColumn=False, showLastColumn=False)
        ws.add_table(tbl)

    if total_row_builder:
        total_vals = total_row_builder(ncols)
        _write_row(ws, r, total_vals, fill=FILL_NAVY, font=FONT_TOTAL, aligns=col_aligns,
                   formats=col_formats, border=True)
        ws.row_dimensions[r].height = ROW_HEIGHT
        r += 1

    return r + 1

def _compute_product_controls(res, tolerances=None):
    m = res['metrics']
    on_hand, depleted, receivings = res['on_hand'], res['depleted'], res['receivings']
    zero_cost_tolerance = (tolerances or {}).get('zero_cost_tolerance', 0.0)
    qty_tol = (tolerances or {}).get('qty_tolerance')
    val_tol = (tolerances or {}).get('value_tolerance')

    oh_qty = sum(l['qty'] for l in on_hand)
    oh_val = sum(l['total_value'] for l in on_hand)
    raw_qty = sum(r['qty'] for r in receivings)
    raw_val = sum(r['total_cost'] for r in receivings)
    dep_qty = sum(d['qty_consumed'] for d in depleted)
    beg_var_qty = res.get('beg_variance_qty', 0)
    beg_var_val = res.get('beg_variance_val', 0)
    processing_error = res.get('processing_error')
    unpriced_count = (
        sum(1 for layer in on_hand if _layer_is_unpriced(layer, zero_cost_tolerance) and layer['qty'] > QTY_TOLERANCE)
        + sum(1 for layer in depleted if _layer_is_unpriced(layer, zero_cost_tolerance) and layer['qty_consumed'] > QTY_TOLERANCE)
    )
    chrono_ok = _check_chronology(depleted) and _check_chronology(on_hand)

    checks = [
        ("Processing Integrity", "FAIL" if processing_error else "PASS", processing_error or "Calculation completed without an exception"),
        ("Beginning Quantity Agreement", "FAIL" if _quantity_is_material(beg_var_qty, qty_tol) else "PASS", f"{beg_var_qty:,.0f} unit variance vs Master Grid — a variance of 0 units is required to close this period" if _quantity_is_material(beg_var_qty, qty_tol) else "0-unit variance"),
        ("Estimated Value Effect of Beginning Qty Variance", "REVIEW" if _value_is_material(beg_var_val, val_tol) else "PASS", f"${beg_var_val:,.2f} estimated effect; the Master Grid supplies quantities, not an independent book value" if _value_is_material(beg_var_val, val_tol) else "$0.00 estimated effect"),
        ("Receipt Quantity Agreement", "FAIL" if _quantity_is_material(raw_qty - m['purch_qty'], qty_tol) else "PASS", "Raw receipts do not sum to purchase quantity" if _quantity_is_material(raw_qty - m['purch_qty'], qty_tol) else f"{raw_qty:,.0f} receipt units tied"),
        ("Receipt Value Agreement", "FAIL" if _value_is_material(raw_val - m['purch_val'], val_tol) else "PASS", "Raw receipts do not sum to purchase value" if _value_is_material(raw_val - m['purch_val'], val_tol) else f"${raw_val:,.2f} receipt value tied"),
        ("Depletion Quantity Agreement", "FAIL" if _quantity_is_material(dep_qty - m['usage_qty'], qty_tol) else "PASS", "Depleted layers do not sum to FIFO usage quantity" if _quantity_is_material(dep_qty - m['usage_qty'], qty_tol) else f"{dep_qty:,.0f} depleted units tied"),
        ("Layer Value Conservation", "FAIL" if _value_is_material(m.get('value_variance', 0), val_tol) else "PASS", f"${m.get('value_variance', 0):,.2f} rollforward variance"),
        ("Ending Quantity to Open Layers", "FAIL" if _quantity_is_material(oh_qty - m['end_qty'], qty_tol) else "PASS", f"Open layers total {oh_qty:,.0f} vs ending {m['end_qty']:,.0f}" if _quantity_is_material(oh_qty - m['end_qty'], qty_tol) else f"Open layers equal {oh_qty:,.0f} units"),
        ("Ending Value to Open Layers", "FAIL" if _value_is_material(oh_val - m['end_val'], val_tol) else "PASS", "Open layer value does not sum to ending value" if _value_is_material(oh_val - m['end_val'], val_tol) else f"Open layers equal ${oh_val:,.2f}"),
        ("FIFO Chronology", "PASS" if chrono_ok else "FAIL", "No chronological violations" if chrono_ok else "Layer dates are not in non-decreasing order"),
        ("Unpriced Layer Count", "PASS" if unpriced_count == 0 else "REVIEW", "0 unpriced open or depleted layers" if unpriced_count == 0 else f"{unpriced_count} open or depleted layer(s) carry a $0 unit cost"),
    ]

    if any(status == "FAIL" for _, status, _ in checks): overall = "FAIL"
    elif any(status == "REVIEW" for _, status, _ in checks): overall = "REVIEW"
    elif not _quantity_is_material(m['purch_qty'], qty_tol) and not _quantity_is_material(m['usage_qty'], qty_tol) and not _quantity_is_material(m['beg_qty'] - m['end_qty'], qty_tol): overall = "PASS - No Activity"
    elif not _quantity_is_material(m['end_qty'], qty_tol) and _value_is_acceptable(m['end_val'], val_tol): overall = "PASS - Zero Balance"
    else: overall = "PASS"

    return overall, checks

def _compute_summary_control_status(res, alias_found=True, alias_ambiguous=False, tolerances=None):
    m = res['metrics']
    on_hand = res['on_hand']
    zero_cost_tolerance = (tolerances or {}).get('zero_cost_tolerance', 0.0)
    qty_tol = (tolerances or {}).get('qty_tolerance')
    val_tol = (tolerances or {}).get('value_tolerance')
    exceptions = []
    hard_fail = False

    if res.get('processing_error'):
        hard_fail = True
        exceptions.append(f"Processing error: {res['processing_error']}")

    if not alias_found:
        hard_fail = True
        exceptions.append("Alias not found in Master Grid")
    elif alias_ambiguous:
        hard_fail = True
        exceptions.append("Alias appears more than once in Master Grid")

    if _quantity_is_material(m['variance_qty'], qty_tol):
        hard_fail = True
        exceptions.append(f"Rollforward quantity does not balance ({m['variance_qty']:,.0f} unit variance)")

    oh_qty = sum(l['qty'] for l in on_hand)
    oh_val = sum(l['total_value'] for l in on_hand)
    if _quantity_is_material(oh_qty - m['end_qty'], qty_tol):
        hard_fail = True
        exceptions.append("Open layers do not equal ending quantity")

    layer_value_fail = _value_is_material(m.get('value_variance', 0), val_tol)
    if layer_value_fail:
        hard_fail = True
        exceptions.append(f"Layer value rollforward does not conserve (${m['value_variance']:,.2f} variance)")

    valuation_fail = _value_is_material(oh_val - m['end_val'], val_tol) or layer_value_fail
    if valuation_fail:
        hard_fail = True
        exceptions.append("Open-layer value does not equal ending value")

    unpriced_qty = sum(layer['qty'] for layer in on_hand if _layer_is_unpriced(layer, zero_cost_tolerance) and layer['qty'] > QTY_TOLERANCE)
    unpriced_usage_qty = sum(layer['qty_consumed'] for layer in res.get('depleted', []) if _layer_is_unpriced(layer, zero_cost_tolerance) and layer['qty_consumed'] > QTY_TOLERANCE)
    valuation_review = _quantity_is_material(unpriced_qty, qty_tol) or _quantity_is_material(unpriced_usage_qty, qty_tol)
    if _quantity_is_material(unpriced_qty, qty_tol): exceptions.append(f"{unpriced_qty:,.0f} unit(s) on hand carry no recorded cost")
    if _quantity_is_material(unpriced_usage_qty, qty_tol): exceptions.append(f"{unpriced_usage_qty:,.0f} depleted unit(s) carried no recorded cost")
    valuation_status = "FAIL" if valuation_fail else ("REVIEW" if valuation_review else "PASS")

    beg_var_qty = res.get('beg_variance_qty', 0)
    reconciliation_status = "PASS"
    if _quantity_is_material(beg_var_qty, qty_tol):
        hard_fail = True
        reconciliation_status = "FAIL"
        exceptions.append(f"Beginning quantity differs from Master Grid by {beg_var_qty:,.0f} units — a variance of 0 units is required before this period can be closed")

    if hard_fail: overall = "FAIL"
    elif reconciliation_status == "REVIEW" or valuation_status == "REVIEW": overall = "REVIEW"
    elif not _quantity_is_material(m['purch_qty'], qty_tol) and not _quantity_is_material(m['usage_qty'], qty_tol) and not _quantity_is_material(m['beg_qty'] - m['end_qty'], qty_tol): overall = "PASS - No Activity"
    elif not _quantity_is_material(m['end_qty'], qty_tol) and _value_is_acceptable(m['end_val'], val_tol): overall = "PASS - Zero Balance"
    else: overall = "PASS"

    return {
        'reconciliation_status': reconciliation_status, 'valuation_status': valuation_status,
        'overall_status': overall, 'exception_message': "; ".join(exceptions) if exceptions else ""
    }

def _status_style(status):
    if status.startswith("FAIL"): return FILL_RED, FONT_RED
    if status.startswith("REVIEW"): return FILL_AMBER, FONT_AMBER
    return FILL_GREEN, FONT_GREEN

def _build_metrics_box(ws, start_row, metrics):
    r = start_row
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
    c = ws.cell(row=r, column=1, value="Current Inventory Metrics")
    c.fill, c.font, c.alignment = FILL_SLATE, FONT_SECTION_LARGE, ALIGN_LEFT
    ws.row_dimensions[r].height = 22
    r += 1

    _write_row(ws, r, ["Metric", "Quantity", "Value"], fill=FILL_HEADER, font=FONT_COLHDR, aligns=[ALIGN_LEFT, ALIGN_RIGHT, ALIGN_RIGHT], border=True)
    ws.row_dimensions[r].height = HEADER_ROW_HEIGHT
    r += 1

    available_qty = metrics['beg_qty'] + metrics['purch_qty']
    available_val = metrics['beg_val'] + metrics['purch_val']

    rows = [
        ("Beginning Inventory", metrics['beg_qty'], metrics['beg_val'], None),
        ("Plus: Current-Period Receipts", metrics['purch_qty'], metrics['purch_val'], None),
        ("Inventory Available", available_qty, available_val, 'subtotal'),
        ("Less: FIFO Usage", -metrics['usage_qty'], -metrics['usage_val'], 'usage'),
        ("Ending Inventory", metrics['end_qty'], metrics['end_val'], 'final'),
    ]
    for label, qty, val, kind in rows:
        font = FONT_NORMAL
        fill = None
        qfmt, vfmt = FMT_QTY, FMT_USD
        if kind == 'usage': qfmt, vfmt = FMT_QTY_PAREN, FMT_USD_PAREN
        if kind == 'subtotal': font, fill = FONT_LABEL, FILL_HEADER
        if kind == 'final': fill, font = FILL_NAVY, FONT_TOTAL
        _write_row(ws, r, [label, qty, val], fill=fill, font=font, aligns=[ALIGN_LEFT, ALIGN_RIGHT, ALIGN_RIGHT], formats=[None, qfmt, vfmt], border=True)
        ws.row_dimensions[r].height = ROW_HEIGHT
        r += 1
    return r + 1

def _build_controls_block(ws, start_row, res, tolerances=None):
    r = start_row
    overall, checks = _compute_product_controls(res, tolerances=tolerances)

    fill, font = _status_style(overall)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
    c = ws.cell(row=r, column=1, value=f"Overall Product Status: {overall}")
    c.fill, c.font, c.alignment = fill, font, ALIGN_LEFT
    for col in range(3, 14): ws.cell(row=r, column=col).fill = fill
    ws.row_dimensions[r].height = ROW_HEIGHT
    banner_row = r
    r += 1

    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
    hdr = ws.cell(row=r, column=1, value="Individual Controls (click the outline − to collapse)")
    hdr.fill, hdr.font, hdr.alignment = FILL_SLATE, FONT_SECTION, ALIGN_LEFT
    r += 1

    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
    ws.cell(row=r, column=1, value="Control").fill = FILL_HEADER
    ws.cell(row=r, column=1).font = FONT_COLHDR
    ws.cell(row=r, column=1).alignment = ALIGN_LEFT
    ws.cell(row=r, column=3, value="Status").fill = FILL_HEADER
    ws.cell(row=r, column=3).font = FONT_COLHDR
    ws.cell(row=r, column=3).alignment = ALIGN_LEFT
    ws.merge_cells(start_row=r, start_column=4, end_row=r, end_column=13)
    detail_hdr = ws.cell(row=r, column=4, value="Detail")
    detail_hdr.fill, detail_hdr.font, detail_hdr.alignment = FILL_HEADER, FONT_COLHDR, ALIGN_LEFT
    for col in range(1, 14): ws.cell(row=r, column=col).border = BORDER_THIN
    ws.row_dimensions[r].height = HEADER_ROW_HEIGHT
    r += 1

    for name, status, detail in checks:
        cfill, cfont = _status_style(status)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        ws.cell(row=r, column=1, value=name).font = FONT_NORMAL
        ws.cell(row=r, column=1).alignment = ALIGN_LEFT
        status_cell = ws.cell(row=r, column=3, value=status)
        status_cell.fill, status_cell.font, status_cell.alignment = cfill, cfont, ALIGN_LEFT
        ws.merge_cells(start_row=r, start_column=4, end_row=r, end_column=13)
        detail_cell = ws.cell(row=r, column=4, value=detail)
        detail_cell.font, detail_cell.alignment = FONT_NORMAL, ALIGN_LEFT
        for col in range(1, 14): ws.cell(row=r, column=col).border = BORDER_THIN
        ws.row_dimensions[r].height = ROW_HEIGHT
        ws.row_dimensions[r].outlineLevel = 1
        r += 1

    return r + 1, banner_row

def _build_product_sheet(wb, res, period, alias_seq, fiscal_year=None, tolerances=None):
    metrics = res['metrics']
    sheet_name = _product_sheet_name(res['alias'], res['prod_name'])
    ws = wb.create_sheet(sheet_name)

    widths = [8, 30, 14, 14, 14, 14, 14, 14, 15, 16, 12, 16, 16]
    for i, w in enumerate(widths, 1): ws.column_dimensions[get_column_letter(i)].width = w

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=13)
    year_label = f"FY{int(fiscal_year)}  |  " if fiscal_year else ""
    title_cell = ws.cell(row=1, column=1, value=f"Alias {res['alias']} — {res['prod_name']}  |  {year_label}Period {period:02d}")
    title_cell.fill, title_cell.font, title_cell.alignment = FILL_NAVY, FONT_TITLE_PRODUCT, ALIGN_LEFT
    ws.row_dimensions[1].height = 26

    back_cell = ws.cell(row=2, column=1, value="↩ Return to Executive Overview")
    back_cell.hyperlink = "#'Executive Summary'!A1"
    back_cell.font = FONT_HYPERLINK
    back_cell.alignment = ALIGN_LEFT
    ws.row_dimensions[2].height = ROW_HEIGHT

    r = 4
    r = _build_metrics_box(ws, r, metrics)
    r, banner_row = _build_controls_block(ws, r, res, tolerances=tolerances)

    ws.freeze_panes = f"A{banner_row + 1}"

    receipts = res['receivings']
    receipt_rows = []
    running_bal = metrics['beg_qty']
    for rec in receipts:
        running_bal += rec['qty']
        receipt_rows.append([rec['date'], rec['po'], rec['invoice'], rec['unit_cost'], rec['total_cost'], rec['qty'], running_bal])

    def receipts_total(ncols):
        avg_cost = metrics['purch_val'] / metrics['purch_qty'] if metrics['purch_qty'] else 0
        return ["Total Received  (Unit Cost = Weighted-Avg Receipt Cost, not a summed rate)", "", "", avg_cost, metrics['purch_val'], metrics['purch_qty'], ""]

    r = _write_table_section(ws, r, "Current Period Receipts", ["Delivery Date", "PO", "Invoice", "Unit Cost", "Total Cost", "Qty Received", "Running Balance"], receipt_rows, col_formats=[None, None, None, FMT_RATE, FMT_USD, FMT_QTY, FMT_QTY], col_aligns=[ALIGN_LEFT, ALIGN_LEFT, ALIGN_LEFT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT], table_name=f"Receipts_A{res['alias']}_{alias_seq}", total_row_builder=receipts_total)

    oh_rows = []
    for oh in res['on_hand']:
        src_period, start_dt, end_dt = _layer_period_and_dates(oh)
        oh_rows.append([oh['seq'], oh['layer_id'], src_period, start_dt, end_dt, oh['original_qty'], oh['qty_depleted'], oh['qty'], oh['unit_cost'], oh['total_value'], oh['age_days'] if oh['age_days'] is not None else "N/A", oh.get('po', ''), oh.get('invoice', '')])

    def on_hand_total(ncols):
        avg_cost = metrics['end_val'] / metrics['end_qty'] if metrics['end_qty'] else 0
        return ["TOTAL ON HAND", "", "", "", "", "", "", metrics['end_qty'], avg_cost, metrics['end_val'], "", "", ""]

    r = _write_table_section(ws, r, "Inventory On Hand", ["Seq", "Layer ID", "Source Period", "Layer Start Date", "Layer End Date", "Original Qty", "Qty Depleted", "Qty Remaining", "Unit Cost", "Remaining Value", "Age (Days)", "Source PO", "Source Invoice"], oh_rows, col_formats=[FMT_QTY, None, None, 'yyyy-mm-dd', 'yyyy-mm-dd', FMT_QTY, FMT_QTY, FMT_QTY, FMT_RATE, FMT_USD, FMT_QTY, None, None], col_aligns=[ALIGN_RIGHT, ALIGN_LEFT, ALIGN_LEFT, ALIGN_LEFT, ALIGN_LEFT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_LEFT, ALIGN_LEFT], table_name=f"OnHand_A{res['alias']}_{alias_seq}", total_row_builder=on_hand_total)

    dep_rows = []
    zero_cost_tolerance = (tolerances or {}).get('zero_cost_tolerance', 0.0)
    has_zero_cost_layer = False
    for dep in res.get('depleted', []):
        unit_cost = float(dep.get('unit_cost') or 0.0)
        has_zero_cost_layer = has_zero_cost_layer or _layer_is_unpriced(dep, zero_cost_tolerance)
        src_period, start_dt, end_dt = _layer_period_and_dates(dep)
        dep_rows.append([
            dep.get('seq'), dep.get('layer_id', ''), src_period, start_dt, end_dt,
            dep.get('qty_available', 0.0), dep.get('qty_consumed', 0.0),
            dep.get('qty_remaining', 0.0), unit_cost,
            dep.get('usage_value', 0.0), dep.get('period', ''), dep.get('status', ''),
        ])

    def depleted_total(ncols):
        if has_zero_cost_layer:
            note = "Avg unit cost omitted - includes $0 legacy carryover layer(s); see individual layer rates above"
            return ["TOTAL DEPLETED", "", "", "", "", "", metrics['usage_qty'], "", note, metrics['usage_val'], "", ""]
        avg_cost = metrics['usage_val'] / metrics['usage_qty'] if metrics['usage_qty'] else 0
        return ["TOTAL DEPLETED  (Unit Cost = Effective Avg Depletion Cost)", "", "", "", "", "", metrics['usage_qty'], "", avg_cost, metrics['usage_val'], "", ""]

    r = _write_table_section(ws, r, "Inventory Depleted", ["Seq", "Layer ID", "Source Period", "Layer Start Date", "Layer End Date", "Qty Available", "Qty Consumed", "Qty Remaining", "Unit Cost", "FIFO Usage Value", "Depletion Period", "Depletion Status"], dep_rows, col_formats=[FMT_QTY, None, None, 'yyyy-mm-dd', 'yyyy-mm-dd', FMT_QTY, FMT_QTY, FMT_QTY, FMT_RATE, FMT_USD, None, None], col_aligns=[ALIGN_RIGHT, ALIGN_LEFT, ALIGN_LEFT, ALIGN_LEFT, ALIGN_LEFT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_LEFT, ALIGN_LEFT], table_name=f"Depleted_A{res['alias']}_{alias_seq}", total_row_builder=depleted_total)

    ws.sheet_properties.outlinePr.summaryBelow = True
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = '1:1'
    ws.print_area = f"A1:M{r}"
    ws.oddHeader.center.text = f"{res['prod_name']} — Period {period:02d}"
    ws.oddFooter.center.text = "Page &P of &N"
    return ws

def _build_executive_summary_sheet(wb, all_results, period, fiscal_year=None, tolerances=None):
    ws = wb.create_sheet("Executive Summary", 0)
    widths = [8, 34, 16, 20, 20, 18, 18, 18, 55]
    for i, w in enumerate(widths, 1): ws.column_dimensions[get_column_letter(i)].width = w

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=9)
    year_label = f"FY{int(fiscal_year)} — " if fiscal_year else ""
    t = ws.cell(row=1, column=1, value=f"Executive Summary — {year_label}Period {period:02d}")
    t.fill, t.font, t.alignment = FILL_NAVY, FONT_TITLE, ALIGN_LEFT
    ws.row_dimensions[1].height = 24

    headers = ["Alias", "Product", "Ending Qty", "Ending Inventory (Value)", "Inventory Depleted (Value)", "Cost per Unit — On Hand", "Cost per Unit — Depleted", "Overall Status", "Exception Message"]
    aligns = [ALIGN_RIGHT, ALIGN_LEFT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_LEFT, ALIGN_LEFT]
    header_row = 3
    _write_row(ws, header_row, headers, fill=FILL_HEADER, font=FONT_COLHDR, aligns=aligns, border=True)
    ws.row_dimensions[header_row].height = HEADER_ROW_HEIGHT
    r = header_row + 1
    first_data_row = r

    any_footnote = False
    zero_cost_tolerance = (tolerances or {}).get('zero_cost_tolerance', 0.0)
    for idx, res in enumerate(all_results):
        m = res['metrics']
        end_qty, end_val = m['end_qty'], m['end_val']
        usage_qty, usage_val = m['usage_qty'], m['usage_val']
        has_zero_cost = any(_layer_is_unpriced(layer, zero_cost_tolerance) for layer in res.get('depleted', []))
        ctrl = _compute_summary_control_status(res, alias_found=res.get('alias_found', True), alias_ambiguous=res.get('alias_ambiguous', False), tolerances=tolerances)

        cost_on_hand = end_val / end_qty if end_qty else None
        cost_depleted = usage_val / usage_qty if usage_qty else None
        product_label = res['prod_name'] + (" *" if has_zero_cost else "")
        if has_zero_cost: any_footnote = True

        row_vals = [res['alias'], product_label, end_qty, end_val, -usage_val, cost_on_hand, cost_depleted, ctrl['overall_status'], ctrl['exception_message']]
        formats = [FMT_QTY, None, FMT_QTY, FMT_USD, FMT_USD_PAREN, FMT_RATE, FMT_RATE, None, None]
        fill = FILL_ALT_ROW if idx % 2 == 1 else None
        _write_row(ws, r, row_vals, fill=fill, font=FONT_NORMAL, aligns=aligns, formats=formats, border=True)
        _link_cell_to_product_sheet(ws.cell(row=r, column=2), res['alias'], res['prod_name'])
        sfill, sfont = _status_style(ctrl['overall_status'])
        ws.cell(row=r, column=8).fill = sfill
        ws.cell(row=r, column=8).font = sfont
        ws.row_dimensions[r].height = ROW_HEIGHT
        r += 1
    last_data_row = r - 1

    tbl = Table(displayName="Executive_Summary_Products", ref=f"A{header_row}:I{last_data_row}")
    tbl.tableStyleInfo = TableStyleInfo(name="TableStyleMedium9", showRowStripes=False, showColumnStripes=False, showFirstColumn=False, showLastColumn=False)
    ws.add_table(tbl)

    if any_footnote:
        r += 1
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=9)
        note = ws.cell(row=r, column=1, value="* Cost per Unit — Depleted includes one or more $0 legacy carryover layers and understates true depletion cost. See the product's own sheet for individual layer rates.")
        note.font = FONT_AMBER

    ws.freeze_panes = f"A{first_data_row}"
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f'{header_row}:{header_row}'
    ws.print_area = f"A1:I{r}"
    ws.oddHeader.center.text = f"Executive Summary — {year_label}Period {period:02d}"
    ws.oddFooter.center.text = "Page &P of &N"
    return ws

def _build_summary_sheet(wb, all_results, period, fiscal_year=None, tolerances=None):
    ws = wb.create_sheet("Summary", 0)
    widths = [8, 34, 14, 16, 14, 16, 14, 16, 14, 16, 21, 21, 18, 16, 20, 55]
    for i, w in enumerate(widths, 1): ws.column_dimensions[get_column_letter(i)].width = w

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=16)
    year_label = f"FY{int(fiscal_year)} — " if fiscal_year else ""
    t = ws.cell(row=1, column=1, value=f"13-Period Batch FIFO Inventory Tracker — {year_label}Period {period:02d} Master Summary")
    t.fill, t.font, t.alignment = FILL_NAVY, FONT_TITLE, ALIGN_LEFT
    ws.row_dimensions[1].height = 24

    r = 3
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=16)
    lc = ws.cell(row=r, column=1, value="Legend & Control Methodology")
    lc.fill, lc.font, lc.alignment = FILL_SLATE, FONT_SECTION, ALIGN_LEFT
    r += 1
    legend_items = [
        (FILL_RED, FONT_RED, "FAIL — An integrity, reconciliation, or FIFO control failed; intervention is required."),
        (FILL_AMBER, FONT_AMBER, "REVIEW — Reconciled, but a valuation or source-data condition requires review."),
        (FILL_GREEN, FONT_GREEN, "PASS / PASS - No Activity / PASS - Zero Balance — Reconciliation and valuation controls are satisfied."),
    ]
    for fill, font, text in legend_items:
        ws.cell(row=r, column=1, value="   ").fill = fill
        c = ws.cell(row=r, column=2, value=text)
        c.font = font
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=16)
        ws.row_dimensions[r].height = ROW_HEIGHT
        r += 1
    r += 1

    headers = ["Alias", "Product", "Beginning Qty", "Beginning Value", "Receipt Qty", "Receipt Value", "FIFO Usage Qty", "FIFO Usage Value", "Ending Qty", "Ending Value", "Beginning Qty Variance", "Estimated Value Effect", "Reconciliation Status", "Valuation Status", "Overall Status", "Exception Message"]
    aligns = [ALIGN_RIGHT, ALIGN_LEFT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_RIGHT, ALIGN_LEFT, ALIGN_LEFT, ALIGN_LEFT, ALIGN_LEFT]
    header_row = r
    _write_row(ws, r, headers, fill=FILL_HEADER, font=FONT_COLHDR, aligns=aligns, border=True)
    ws.row_dimensions[header_row].height = HEADER_ROW_HEIGHT
    r += 1
    first_data_row = r

    pass_count = review_count = fail_count = 0
    totals = dict(beg_qty=0, beg_val=0, purch_qty=0, purch_val=0, usage_qty=0, usage_val=0, end_qty=0, end_val=0, var_qty=0, var_val=0)

    for idx, res in enumerate(all_results):
        m = res['metrics']
        var_qty = res.get('beg_variance_qty', 0)
        var_val = res.get('beg_variance_val', 0)
        ctrl = _compute_summary_control_status(res, alias_found=res.get('alias_found', True), alias_ambiguous=res.get('alias_ambiguous', False), tolerances=tolerances)

        for k, v in [('beg_qty', m['beg_qty']), ('beg_val', m['beg_val']), ('purch_qty', m['purch_qty']), ('purch_val', m['purch_val']), ('usage_qty', -m['usage_qty']), ('usage_val', -m['usage_val']), ('end_qty', m['end_qty']), ('end_val', m['end_val']), ('var_qty', var_qty), ('var_val', var_val)]:
            totals[k] += v

        if ctrl['overall_status'] == 'FAIL': fail_count += 1
        elif ctrl['overall_status'] == 'REVIEW': review_count += 1
        else: pass_count += 1

        row_vals = [res['alias'], res['prod_name'], m['beg_qty'], m['beg_val'], m['purch_qty'], m['purch_val'], -m['usage_qty'], -m['usage_val'], m['end_qty'], m['end_val'], var_qty, var_val, ctrl['reconciliation_status'], ctrl['valuation_status'], ctrl['overall_status'], ctrl['exception_message']]
        formats = [FMT_QTY, None, FMT_QTY, FMT_USD, FMT_QTY, FMT_USD, FMT_QTY_PAREN, FMT_USD_PAREN, FMT_QTY, FMT_USD, FMT_QTY_PAREN, FMT_USD_PAREN, None, None, None, None]
        fill = FILL_ALT_ROW if idx % 2 == 1 else None
        _write_row(ws, r, row_vals, fill=fill, font=FONT_NORMAL, aligns=aligns, formats=formats, border=True)
        _link_cell_to_product_sheet(ws.cell(row=r, column=2), res['alias'], res['prod_name'])

        for col, status in [(13, ctrl['reconciliation_status']), (14, ctrl['valuation_status']), (15, ctrl['overall_status'])]:
            sfill, sfont = _status_style(status)
            ws.cell(row=r, column=col).fill = sfill
            ws.cell(row=r, column=col).font = sfont
        ws.row_dimensions[r].height = ROW_HEIGHT
        r += 1
    last_data_row = r - 1

    tbl = Table(displayName="Summary_Products", ref=f"A{header_row}:P{last_data_row}")
    tbl.tableStyleInfo = TableStyleInfo(name="TableStyleMedium9", showRowStripes=False, showColumnStripes=False, showFirstColumn=False, showLastColumn=False)
    ws.add_table(tbl)

    total_row = r
    _write_row(ws, total_row, ["WORKBOOK TOTAL", "", totals['beg_qty'], totals['beg_val'], totals['purch_qty'], totals['purch_val'], totals['usage_qty'], totals['usage_val'], totals['end_qty'], totals['end_val'], totals['var_qty'], totals['var_val'], "", "", f"{pass_count} PASS / {review_count} REVIEW / {fail_count} FAIL", ""], fill=FILL_NAVY, font=FONT_TOTAL, aligns=aligns, formats=[FMT_QTY, None, FMT_QTY, FMT_USD, FMT_QTY, FMT_USD, FMT_QTY_PAREN, FMT_USD_PAREN, FMT_QTY, FMT_USD, FMT_QTY_PAREN, FMT_USD_PAREN, None, None, None, None], border=True)
    ws.row_dimensions[total_row].height = ROW_HEIGHT

    ws.freeze_panes = f"A{first_data_row}"
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f'{header_row}:{header_row}'
    ws.print_area = f"A1:P{total_row}"
    ws.oddHeader.center.text = f"Period {period:02d} Master Summary"
    ws.oddFooter.center.text = "Page &P of &N"
    return ws

def _build_run_issues_sheet(wb, run_issues):
    has_blocking_errors = any(sev == 'ERROR' for sev, _ in run_issues)
    ws = wb.create_sheet("Input Exceptions", 2)
    ws.column_dimensions['A'].width = 14
    ws.column_dimensions['B'].width = 110
    ws.merge_cells('A1:B1')
    title = ws['A1']
    title.value = ("Input Exceptions — Preview Cannot Be Committed" if has_blocking_errors else "Input Exceptions — Warnings Only (Commit Not Blocked)")
    title.fill, title.font, title.alignment = FILL_NAVY, FONT_TITLE, ALIGN_LEFT
    _write_row(ws, 3, ["Severity", "Message"], fill=FILL_HEADER, font=FONT_COLHDR, aligns=[ALIGN_LEFT, ALIGN_LEFT], border=True)
    ws.row_dimensions[3].height = HEADER_ROW_HEIGHT
    row = 4
    for severity, message in run_issues:
        fill, font = (FILL_RED, FONT_RED) if severity == 'ERROR' else (FILL_AMBER, FONT_AMBER)
        _write_row(ws, row, [severity, message], fill=fill, font=font, aligns=[ALIGN_LEFT, ALIGN_LEFT], border=True)
        ws.cell(row=row, column=2).alignment = Alignment(horizontal='left', vertical='top', wrap_text=True)
        ws.row_dimensions[row].height = 30
        row += 1
    ws.freeze_panes = 'A4'
    return ws

def export_master_excel(all_results, period, fiscal_year=None, run_issues=None, tolerances=None):
    output = io.BytesIO()
    wb = Workbook()
    wb.remove(wb.active)

    ordered_results = sorted(all_results, key=lambda r: r['alias'])

    _build_summary_sheet(wb, ordered_results, period, fiscal_year=fiscal_year, tolerances=tolerances)
    _build_executive_summary_sheet(wb, ordered_results, period, fiscal_year=fiscal_year, tolerances=tolerances)
    if run_issues:
        _build_run_issues_sheet(wb, run_issues)
    for i, res in enumerate(ordered_results):
        _build_product_sheet(wb, res, period, alias_seq=i, fiscal_year=fiscal_year, tolerances=tolerances)

    # Modern, decluttered look across every sheet in the workbook: no default
    # Excel gridlines competing with the shaded header/total rows and borders
    # already drawn in by _write_row / BORDER_THIN.
    for sheet in wb.worksheets:
        sheet.sheet_view.showGridLines = False

    wb.active = 0
    wb.save(output)
    return output.getvalue()