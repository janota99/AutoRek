"""The Open Item Aids sheet: review context for the unresolved items, beside the Unresolved Exceptions sheet.

It writes ``result.open_item_insights`` (built by ``matching/open_items.py``) as static values, so the sheet and the
Analytics tab on screen always agree. Nothing here computes a match, a total, or a journal-entry figure; none of it
is read by any other sheet.
"""

from __future__ import annotations

import pandas as pd
from openpyxl import Workbook

from ..config import NAVY, SLATE
from ..excel_styles import _prepare_sheet, _write_caption_band, _write_title_band
from ..matching import ReconciliationResult
from ..matching.open_items import SECTION_AGE, SECTION_CUSTOMER, SECTION_SAME_AMOUNT
from .audit import _write_table
from .tables import OPEN_ITEM_AIDS_SHEET, _fiscal_period_prefix

_END_COL = 5

_SECTIONS = (
    (SECTION_SAME_AMOUNT, "SAME AMOUNT ON THE OTHER SIDE", NAVY,
     "Unmatched rows that have an unresolved row at the identical amount on the other side. Counts and dollars "
     "only: no row is named or paired, because equal amounts are not evidence."),
    (SECTION_AGE, "AGE OF UNRESOLVED QUICKBOOKS ITEMS", SLATE,
     "Fiscal periods back from the selected period; share is of the absolute value of the unresolved rows."),
    (SECTION_CUSTOMER, "UNRESOLVED QUICKBOOKS VALUE BY CUSTOMER", NAVY,
     "The ten largest customers by absolute value, then everyone else; share is of the absolute value of the "
     "unresolved rows."),
)


def build_open_items_sheet(wb: Workbook, result: ReconciliationResult) -> None:
    ws = wb.create_sheet(OPEN_ITEM_AIDS_SHEET)
    _write_title_band(ws, 1, 1, _END_COL, f"{_fiscal_period_prefix(result)} - OPEN ITEM AIDS", NAVY)
    _write_caption_band(
        ws, 2, 1, _END_COL,
        "Context for working the unresolved items. None of it creates or suggests a match: every item stays "
        "unresolved until a PO or invoice supports it. Values are fixed at the moment of export.",
        NAVY,
    )
    insights = result.open_item_insights
    row = 4
    if insights is None or insights.empty:
        _write_table(ws, row, pd.DataFrame(), "OPEN ITEMS", "There are no unresolved items in this run.", SLATE, set())
    else:
        for section, title, color, caption in _SECTIONS:
            block = insights.loc[insights["Section"] == section].drop(columns=["Section"])
            if block.empty:
                continue
            if section != SECTION_SAME_AMOUNT:
                block = block.drop(columns=["Note"])
            row = _write_table(ws, row, block.reset_index(drop=True), title, caption, color, {"Amount"})
    for col, width in zip("ABCDE", (70, 10, 18, 22, 70)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A4"
    _prepare_sheet(ws, landscape=True)
