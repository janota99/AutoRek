# upload_templates.py
"""
Downloadable starter workbooks for the two uploads the app consumes.

This module renders zero business logic of its own — it exists purely so a
user can get a correctly-shaped, correctly-typed spreadsheet to fill in,
instead of guessing at column names from the app's error messages. Every
column name and format used here is exactly what ingestion.py's
`find_column` / `parse_period_column_header` / `prepare_receipts_upload`
already recognize, so a template filled in as instructed and re-uploaded
unmodified will pass validation immediately.

Two workbooks are produced:
  - build_master_grid_template(): the Ending Inventory Master Grid, one row
    per known product alias, with a "01".."13" quantity column and a
    matching "01V".."13V" value column for every fiscal period.
  - build_receipts_template(): the Current Period Receipts layout, with one
    illustrative example row plus a block of blank, pre-formatted rows.

build_upload_templates_zip() bundles both into a single .zip for a one-click
download; each is also available standalone for a two-button layout.
"""
import io
from copy import copy
import zipfile
from datetime import date

from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

from .user_inputs import PRODUCTS, QTY_TOLERANCE
from .excel_export import (
    FONT_TITLE, FONT_COLHDR, FONT_NORMAL, FILL_NAVY, FILL_HEADER, FILL_ALT_ROW,
    BORDER_THIN, ALIGN_LEFT, ALIGN_RIGHT, FMT_QTY, FMT_USD, HEADER_ROW_HEIGHT,
)

_INSTRUCTIONS_TITLE_FILL = FILL_NAVY
_INSTRUCTIONS_TITLE_FONT = FONT_TITLE


def _write_instructions_sheet(wb, title, paragraphs):
    ws = wb.create_sheet("Instructions")
    ws.column_dimensions['A'].width = 108
    ws.merge_cells('A1:A1')
    t = ws.cell(row=1, column=1, value=title)
    t.fill, t.font, t.alignment = _INSTRUCTIONS_TITLE_FILL, _INSTRUCTIONS_TITLE_FONT, ALIGN_LEFT
    ws.row_dimensions[1].height = 26
    paragraph_alignment = copy(ALIGN_LEFT)
    paragraph_alignment.wrap_text = True
    paragraph_alignment.vertical = 'top'
    r = 3
    for para in paragraphs:
        c = ws.cell(row=r, column=1, value=para)
        c.font = FONT_NORMAL
        c.alignment = paragraph_alignment
        ws.row_dimensions[r].height = 30 + 15 * (len(para) // 100)
        r += 2
    return ws


def build_master_grid_template():
    """Return the .xlsx bytes for a blank, correctly-shaped Master Grid."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Master Grid Template"

    headers = ['PRODUCT ALIAS', 'DESCRIPTION']
    for period in range(1, 14):
        headers.append(f"{period:02d}")
        headers.append(f"{period:02d}V")

    widths = [12, 32] + [10, 12] * 13
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    for col, name in enumerate(headers, 1):
        c = ws.cell(row=1, column=col, value=name)
        c.fill, c.font, c.border = FILL_HEADER, FONT_COLHDR, BORDER_THIN
        c.alignment = ALIGN_LEFT if col <= 2 else ALIGN_RIGHT
    ws.row_dimensions[1].height = HEADER_ROW_HEIGHT
    ws.freeze_panes = "C2"

    qty_col_letters = []
    for row_idx, (alias, name) in enumerate(sorted(PRODUCTS.items()), start=2):
        fill = FILL_ALT_ROW if row_idx % 2 == 0 else None
        c = ws.cell(row=row_idx, column=1, value=alias)
        c.font, c.border, c.alignment = FONT_NORMAL, BORDER_THIN, ALIGN_RIGHT
        if fill: c.fill = fill
        c = ws.cell(row=row_idx, column=2, value=name)
        c.font, c.border, c.alignment = FONT_NORMAL, BORDER_THIN, ALIGN_LEFT
        if fill: c.fill = fill
        col = 3
        for period in range(1, 14):
            qty_cell = ws.cell(row=row_idx, column=col, value=0.0)
            qty_cell.number_format = FMT_QTY
            qty_cell.font, qty_cell.border, qty_cell.alignment = FONT_NORMAL, BORDER_THIN, ALIGN_RIGHT
            if fill: qty_cell.fill = fill
            if row_idx == 2:
                qty_col_letters.append(get_column_letter(col))
            col += 1
            val_cell = ws.cell(row=row_idx, column=col, value=0.0)
            val_cell.number_format = FMT_USD
            val_cell.font, val_cell.border, val_cell.alignment = FONT_NORMAL, BORDER_THIN, ALIGN_RIGHT
            if fill: val_cell.fill = fill
            col += 1
        ws.row_dimensions[row_idx].height = 16
    last_row = 1 + len(PRODUCTS)

    # Guard against the single most common data-entry mistake: a negative
    # quantity. Value columns are intentionally left unrestricted -- a known
    # dollar variance can legitimately run either direction.
    dv = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1=str(-QTY_TOLERANCE),
                         showErrorMessage=True, errorTitle="Negative quantity",
                         error="Ending inventory quantities cannot be negative.")
    ws.add_data_validation(dv)
    for letter in qty_col_letters:
        dv.add(f"{letter}2:{letter}{last_row}")

    dv_alias = DataValidation(type="list", formula1=f'"{",".join(str(a) for a in sorted(PRODUCTS))}"',
                               showErrorMessage=False)
    ws.add_data_validation(dv_alias)
    dv_alias.add(f"A2:A{last_row}")

    _write_instructions_sheet(wb, "Master Grid Upload — Instructions", [
        "This workbook has one row per product, pre-filled with every PRODUCT ALIAS and DESCRIPTION the app "
        "currently knows about. Do not add, remove, renumber, or reorder rows or columns, and do not rename "
        "the header row in row 1 — the app matches everything by exact column header text and by PRODUCT "
        "ALIAS, not by row position.",
        "PRODUCT ALIAS must exactly match one of the numbers already in this template (2-30 currently). "
        "DESCRIPTION is for your own reference only and is never read by the app.",
        "Columns \"01\" through \"13\" are the ENDING INVENTORY QUANTITY for that fiscal period. Quantities "
        "must be zero or positive -- never negative.",
        "Columns \"01V\" through \"13V\" are the matching ENDING INVENTORY VALUE ($) for that same fiscal "
        "period, taken straight from your own spreadsheet's Ending Inventory / TOTAL calculation. Unlike "
        "quantities, a value CAN be negative -- a small known dollar variance between your book value and the "
        "app's FIFO layers is expected and is allowed to run in either direction.",
        "Period 1's BEGINNING balance is read from column \"13\" / \"13V\" -- that column must represent the "
        "prior fiscal year's ending quantity and value, not period 1 itself.",
        "Before the app will accept this upload for a given fiscal period, every product needs both the "
        "quantity AND value columns filled in for the period you are closing AND the period immediately before "
        "it (the app reads the earlier one as that product's beginning balance). Every other period column can "
        "be left at 0 until you reach it.",
        "Type plain numbers into every quantity and value cell (Excel's own currency/number formatting is fine "
        "-- the app understands $ signs, thousands commas, and parentheses for negatives -- but plain numbers "
        "are the safest if you are typing by hand).",
    ])

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


def build_receipts_template():
    """Return the .xlsx bytes for a blank, correctly-shaped Receipts upload."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Receipts Template"

    headers = ['PRODUCT ALIAS', 'DESCRIPTION', 'ITEMS', 'COMPANY', 'DATE DELIVERED',
               'LOT #', 'BOL #', 'SEAL #', 'PO #', 'QUANTITY', 'PRICE', 'PERIOD', 'INVOICE #']
    widths = [12, 24, 20, 20, 16, 14, 12, 12, 12, 12, 14, 10, 14]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    for col, name in enumerate(headers, 1):
        c = ws.cell(row=1, column=col, value=name)
        c.fill, c.font, c.border = FILL_HEADER, FONT_COLHDR, BORDER_THIN
        c.alignment = ALIGN_LEFT if col in (2, 3, 4) else ALIGN_RIGHT
    ws.row_dimensions[1].height = HEADER_ROW_HEIGHT
    ws.freeze_panes = "A2"

    example_alias = min(PRODUCTS)
    example_row = [
        example_alias, PRODUCTS[example_alias], 'Example item description', 'Example Supplier Co.',
        date.today(), 'L6-000000', 'BOL-00000', 'SEAL-000', 'PO-00000', 10000, 250.00, 12, 'INV-00000',
    ]
    for col, val in enumerate(example_row, 1):
        c = ws.cell(row=2, column=col, value=val)
        c.font, c.border = FONT_NORMAL, BORDER_THIN
        c.alignment = ALIGN_LEFT if col in (2, 3, 4) else ALIGN_RIGHT
    ws.cell(row=2, column=5).number_format = 'yyyy-mm-dd'
    ws.cell(row=2, column=10).number_format = FMT_QTY
    ws.cell(row=2, column=11).number_format = FMT_USD

    blank_rows = 40
    for row_idx in range(3, 3 + blank_rows):
        for col in range(1, len(headers) + 1):
            c = ws.cell(row=row_idx, column=col)
            c.border = BORDER_THIN
            c.font = FONT_NORMAL
        ws.cell(row=row_idx, column=5).number_format = 'yyyy-mm-dd'
        ws.cell(row=row_idx, column=10).number_format = FMT_QTY
        ws.cell(row=row_idx, column=11).number_format = FMT_USD
        ws.cell(row=row_idx, column=12).number_format = FMT_QTY
        ws.row_dimensions[row_idx].height = 16
    last_row = 2 + blank_rows

    dv_alias = DataValidation(type="list", formula1=f'"{",".join(str(a) for a in sorted(PRODUCTS))}"',
                               showErrorMessage=False)
    ws.add_data_validation(dv_alias)
    dv_alias.add(f"A2:A{last_row}")

    dv_qty = DataValidation(type="decimal", operator="greaterThan", formula1="0",
                             showErrorMessage=True, errorTitle="Invalid quantity",
                             error="Receipt quantity must be a positive number.")
    ws.add_data_validation(dv_qty)
    dv_qty.add(f"J2:J{last_row}")

    _write_instructions_sheet(wb, "Receipts Upload — Instructions", [
        "Row 2 is a filled-in example -- replace it (or delete it) with your own data; it is not read as a "
        "real receipt because it is not the current period's PERIOD value once you change it. Add one row per "
        "receiving line below it, in any order.",
        "PRODUCT ALIAS must exactly match a known product alias. DESCRIPTION, ITEMS, COMPANY, LOT #, BOL #, "
        "SEAL #, PO #, and INVOICE # are all free text and are never validated -- fill in whatever your "
        "receiving paperwork uses, or leave any of them blank.",
        "QUANTITY is the number of units received on that line and must be a positive number.",
        "PRICE is the line's TOTAL EXTENDED VALUE in dollars -- quantity times unit cost -- not a per-unit "
        "rate. The app divides this by QUANTITY itself to work out the unit cost.",
        "DATE DELIVERED must be a real Excel date (use Excel's date picker or type it so Excel recognizes it "
        "as a date, not as text) -- this date is what determines FIFO layer ordering.",
        "PERIOD identifies which fiscal period a receipt belongs to. Plain numbers (\"12\"), or common labels "
        "like \"P12\", \"PD12\", or \"P.12\", are all recognized. Only rows whose PERIOD matches the fiscal "
        "period you are currently processing in the app are pulled into that period's calculation -- rows for "
        "other periods are simply left out, not treated as errors. If you omit the PERIOD column entirely, "
        "every valid row in the file is treated as belonging to the period you are currently processing.",
        "Rows missing PRODUCT ALIAS, QUANTITY, PRICE, or DATE DELIVERED -- or with an unrecognized alias, a "
        "zero/negative quantity, or a negative total value -- are rejected with the source row number called "
        "out, rather than silently dropped.",
    ])

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


def build_upload_templates_zip():
    """Bundle both templates into a single .zip for a one-click download."""
    master_bytes = build_master_grid_template()
    receipts_bytes = build_receipts_template()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Master_Grid_Upload_Template.xlsx", master_bytes)
        zf.writestr("Receipts_Upload_Template.xlsx", receipts_bytes)
    return buf.getvalue()