"""The Posting Summary and Aggregates sheets."""

from __future__ import annotations

from typing import Optional

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..config import (
    ACCOUNTING_COUNT_FORMAT,
    ACCOUNTING_CURRENCY_FORMAT,
    AMBER,
    FONT_NAME,
    FONT_NAME_NUMERIC,
    GREEN_LIGHT,
    NAVY,
    NAVY_LIGHT,
    NEUTRAL_GOLD_FILL,
    NEUTRAL_GOLD_TEXT,
    RED_LIGHT,
    SLATE,
    SLATE_LIGHT,
    TEAL,
    TEXT,
    WHITE,
)
from ..excel_styles import (
    _apply_number_formats,
    _format_body_block,
    _format_header,
    _pin_column_width,
    _prepare_sheet,
    _set_widths,
    _thin_border,
    _total_border,
    _write_caption_band,
    _write_title_band,
    _write_total_row,
    fix_row_height,
)
from ..matching import parse_fiscal_period, ReconciliationResult
from .tables import (
    _je_support_manual_exclusions_expr,
    _review_holds_released_expr,
    _write_dataframe_values,
)


_AGGREGATE_QUANTITY_HEADERS = {"Bottles per Case", "Case Quantity"}


def _write_aggregate_table(
    ws, start_row: int, frame: pd.DataFrame, *,
    title: str, populated_caption: str, empty_caption: str,
    color: str, value_header: str, bottle_formula: bool = False,
) -> int:
    """Write one plain totals table (title band, caption, header, body,
    total row) starting at start_row. Returns the row number of the blank
    spacer row immediately after it, so the caller can chain another table
    beneath it on the same sheet.

    With bottle_formula, each Bottle Count that has a pack size becomes a live
    "=Bottles per Case x Case Quantity" formula (and its total a SUM);
    otherwise Bottle Count is written as the stored value."""
    headers = list(frame.columns)
    end_col = len(headers)
    caption_row = start_row + 1
    header_row = caption_row + 1
    data_row = header_row + 1
    _write_title_band(ws, start_row, 1, end_col, title, color)
    _write_caption_band(
        ws, caption_row, 1, end_col,
        populated_caption if len(frame) else empty_caption,
        color,
    )
    _write_dataframe_values(ws, frame, header_row, 1)
    _format_header(
        ws, header_row, 1, end_col, color, headers=headers,
        amount_columns={value_header}, quantity_columns=_AGGREGATE_QUANTITY_HEADERS,
    )
    last_row = header_row + len(frame)
    bottle_letter = get_column_letter(headers.index("Bottle Count") + 1)
    if len(frame) and bottle_formula:
        size_letter = get_column_letter(headers.index("Bottles per Case") + 1)
        cases_letter = get_column_letter(headers.index("Case Quantity") + 1)
        for row in range(data_row, last_row + 1):
            if ws[f"{size_letter}{row}"].value is not None:
                ws[f"{bottle_letter}{row}"] = f"={size_letter}{row}*{cases_letter}{row}"
    if len(frame):
        _format_body_block(ws, data_row, last_row, 1, end_col, NAVY_LIGHT)
        _apply_number_formats(
            ws, headers, data_row, last_row, 1, {value_header}, _AGGREGATE_QUANTITY_HEADERS,
        )
    total_row = last_row + 1
    totals = {
        "Case Quantity": float(frame["Case Quantity"].sum()),
        value_header: float(frame[value_header].sum()),
    } if len(frame) else {}
    if len(frame) and frame["Bottle Count"].notna().any():
        totals["Bottle Count"] = (
            f"=SUM({bottle_letter}{data_row}:{bottle_letter}{last_row})"
            if bottle_formula else float(frame["Bottle Count"].sum())
        )
    _write_total_row(ws, total_row, 1, end_col, totals, headers)
    _set_widths(ws, 1, end_col, header_row, total_row)
    ws.column_dimensions["A"].width = 34
    if len(frame):
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(end_col)}{last_row}"
    return total_row + 2


def _write_product_review_items(ws, start_row: int, items: pd.DataFrame) -> int:
    """The QuickBooks lines behind the product table's review rows, by
    original description -- or a single "No items need review." line.
    Returns the row for the next table."""
    if items.empty:
        cell = ws.cell(start_row, 1, "No items need review.")
        cell.font = Font(name=FONT_NAME, size=9, italic=True, color=SLATE)
        cell.alignment = Alignment(horizontal="left", vertical="center")
        fix_row_height(ws, start_row, 18)
        return start_row + 2
    headers = list(items.columns)
    end_col = len(headers)
    header_row = start_row + 2
    data_row = header_row + 1
    last_row = header_row + len(items)
    _write_title_band(ws, start_row, 1, end_col, "ITEMS NEEDING REVIEW", NEUTRAL_GOLD_TEXT)
    _write_caption_band(
        ws, start_row + 1, 1, end_col,
        "QuickBooks lines whose product or pack size could not be confirmed. They are included in "
        "the Case Quantity and Value totals above but have no bottle count. Correct the description "
        "or confirm the pack size before relying on the bottle totals.",
        NEUTRAL_GOLD_TEXT,
    )
    _write_dataframe_values(ws, items, header_row, 1)
    _format_header(
        ws, header_row, 1, end_col, NEUTRAL_GOLD_TEXT, headers=headers,
        amount_columns={"Amount"}, quantity_columns={"Case Quantity"},
    )
    _format_body_block(ws, data_row, last_row, 1, end_col, NAVY_LIGHT)
    _apply_number_formats(ws, headers, data_row, last_row, 1, {"Amount"}, {"Case Quantity"})
    totals = {
        "Case Quantity": float(items["Case Quantity"].sum()),
        "Amount": float(items["Amount"].sum()),
    }
    _write_total_row(ws, last_row + 1, 1, end_col, totals, headers)
    return last_row + 3


def _customer_bottle_note(result: ReconciliationResult) -> str:
    """Caption sentence naming the cases the customer Bottle Count leaves out."""
    if not result.qb_mapping.get("product"):
        return " Bottle Count is blank because no QuickBooks product description column is mapped."
    missing = result.customer_cases_without_bottles
    if not missing:
        return " Bottle Count is summed line by line, since a customer can buy several pack sizes."
    names = [f"{name} ({cases:,.0f})" for name, cases in missing.items()]
    shown = ", ".join(names[:5]) + (f", and {len(names) - 5} more" if len(names) > 5 else "")
    return (
        f" Bottle Count is summed line by line and leaves out {sum(missing.values()):,.0f} case(s) "
        f"with no known pack size: {shown}. See Items needing review."
    )


def build_aggregates_sheet(
    wb: Workbook, result: ReconciliationResult, *, sheet_title: str = "Product Aggregate Summary",
) -> None:
    """Plain case, bottle, and value totals by product, and by QuickBooks
    Customer -- used for bottle-count and customer-volume reconciliation,
    not a matching decision view, so it deliberately does not compute
    match rates or JE Support/Review Hold breakdowns (see
    build_product_summary / build_customer_summary in matching/summaries.py).

    sheet_title defaults to the Legacy workbook's tab name; the primary
    workbook passes its own shorter "Aggregates" tab name."""
    ws = wb.create_sheet(sheet_title)
    selected_period = result.metadata.get("fiscal_period")
    period_scope = (
        f"Only primary QuickBooks rows from Period {int(selected_period):02d} are included."
        if selected_period is not None
        else "All primary QuickBooks fiscal periods are included."
    )
    next_row = _write_aggregate_table(
        ws, 1, result.product_summary,
        title="PRODUCT AGGREGATE SUMMARY",
        populated_caption="Cases, bottles, and value by product, for bottle-count reconciliation. "
        "QuickBooks QTY is a case count; Bottle Count = Bottles per Case × Case Quantity. "
        f"{period_scope}",
        empty_caption="A QuickBooks quantity column, an amount column, and a product description "
        "column are not all mapped, so no product breakdown is available.",
        color=NAVY, value_header="Product Value", bottle_formula=True,
    )
    if len(result.product_summary):
        next_row = _write_product_review_items(ws, next_row, result.product_review_items)
    _write_aggregate_table(
        ws, next_row, result.customer_summary,
        title="CUSTOMER AGGREGATE SUMMARY",
        populated_caption=f"Cases, bottles, and value by QuickBooks Customer. {period_scope}"
        + _customer_bottle_note(result),
        empty_caption="A QuickBooks quantity column, an amount column, and a Customer column are "
        "not all mapped, so no customer breakdown is available.",
        color=TEAL, value_header="Customer Value",
    )
    ws.freeze_panes = "A4"
    _prepare_sheet(ws, landscape=False)


_POSTING_SUMMARY_END_COL = 8


POSTING_SUMMARY_COLUMN_C_WIDTH = 16.0


# The four top-level dispositions, in this fixed order, everywhere the
# workbook shows them as a set -- the Posting Summary cards, the equation, and
# the color used for each on both.

_DISPOSITION_CARD_ORDER = (
    ("Matched", GREEN_LIGHT),
    ("True Unmatched", NAVY_LIGHT),
    ("Review Hold", AMBER),
    ("Duplicate Excluded", RED_LIGHT),
)


def _fiscal_period_consistency_warning(result: ReconciliationResult) -> Optional[str]:
    """None if the selected fiscal period looks consistent with the primary
    QuickBooks data, otherwise a one-sentence warning naming the mismatch --
    a large, unmissable banner is more useful before export than a silent
    misclassification (see the fiscal-period rule in matching/core.py)."""
    selected_period = result.metadata.get("fiscal_period")
    period_col = result.qb_mapping.get("period")
    if selected_period is None or not period_col or not len(result.qb_work):
        return None
    default_year = int(result.metadata.get("fiscal_year") or result.run_timestamp.year)
    periods = [
        parse_fiscal_period(value, default_year)[0]
        for value in result.qb_work[period_col]
    ]
    read = [period for period in periods if period is not None]
    if not read:
        return None
    share_selected = sum(1 for period in read if period == int(selected_period)) / len(read)
    if share_selected >= 0.5:
        return None
    from collections import Counter

    most_common_period, most_common_count = Counter(read).most_common(1)[0]
    return (
        f"Only {share_selected:.0%} of QuickBooks rows with a readable fiscal period belong to the "
        f"selected period PD-{int(selected_period):02d} -- most ({most_common_count:,} of {len(read):,}) are "
        f"PD-{most_common_period:02d}. Verify PD-{int(selected_period):02d} is the intended reporting period "
        "before relying on this run's period classifications."
    )


def build_posting_summary_sheet(wb: Workbook, result: ReconciliationResult) -> None:
    """The landing page: everything a reviewer needs before opening any other
    sheet -- the selected fiscal period (impossible to miss), a single
    accounted-for-rows control, a KPI ribbon of the four final dispositions
    against the source total, and the journal-entry bridge. Every number
    here is read straight from result.metrics / result.qb_dispositions, the
    same source every other sheet uses, so this page can never disagree with
    the detail behind it.

    Deliberately merge-free (aside from the one wrapped, conditional warning
    banner): a screen reader tabbing across a merged region can get stuck or
    skip cells entirely, so every spanning band here uses either plain left-
    aligned overflow (a single line of unwrapped text painted across
    identically-filled, otherwise-empty cells -- Excel renders the overflow
    without needing the cells joined) or "center across selection"
    (Alignment(horizontal="centerContinuous"), set on every cell in the
    range) for banners that need to stay centered. Run ID and the generation
    timestamp live in the page footer (see _apply_workbook_run_metadata),
    not in the on-sheet caption, so they never compete with the figures.
    """
    ws = wb.create_sheet("Posting Summary")
    end_col = _POSTING_SUMMARY_END_COL
    metrics = result.metrics
    control_ok = metrics["Control Status"] == "PASS"

    def unmerged_band(row: int, text: str, fill_color: str, font: Font, *, height: float) -> None:
        """A single-line, left-aligned title/label spanning the row without
        merging: text in the first cell only, matching fill on every cell in
        the range so the overflow reads as one continuous band."""
        for col in range(1, end_col + 1):
            cell = ws.cell(row, col)
            if col == 1:
                cell.value = text
            cell.fill = PatternFill("solid", fgColor=fill_color)
            cell.font = font
            cell.alignment = Alignment(horizontal="left", vertical="center")
        fix_row_height(ws, row, height)

    def centered_band(row: int, text: str, fill_color: str, font: Font, *, height: float) -> None:
        """A single-line banner that stays centered across the full row
        without merging, using Excel's native "center across selection"."""
        for col in range(1, end_col + 1):
            cell = ws.cell(row, col)
            if col == 1:
                cell.value = text
            cell.fill = PatternFill("solid", fgColor=fill_color)
            cell.font = font
            cell.alignment = Alignment(horizontal="centerContinuous", vertical="center")
        fix_row_height(ws, row, height)

    unmerged_band(
        1, "QBO RECONCILIATION POSTING SUMMARY", NAVY,
        Font(name=FONT_NAME, size=12, bold=True, color=WHITE), height=27,
    )
    _write_caption_band(
        ws, 2, 1, end_col,
        "Every figure on this page is read from the same controls behind this workbook's "
        "Unresolved Exceptions and Reconciliation Detail sheets.",
        NAVY,
    )

    # The selected fiscal period, impossible to miss.
    period_row = 4
    selected_period = result.metadata.get("fiscal_period")
    fiscal_year = result.metadata.get("fiscal_year")
    period_text = (
        f"CURRENT RECONCILIATION PERIOD: PD-{int(selected_period):02d}"
        + (f" - {int(fiscal_year)}" if fiscal_year else "")
        if selected_period is not None
        else "CURRENT RECONCILIATION PERIOD: NOT SELECTED"
    )
    centered_band(period_row, period_text, SLATE, Font(name=FONT_NAME, size=16, bold=True, color=WHITE), height=30)
    next_row = period_row + 1

    # The one deliberate exception to the no-merge rule: a conditional,
    # multi-sentence warning that must wrap, which "center across selection"
    # does not support -- rare enough (only when the selected period looks
    # inconsistent with the data) that a single merged band here is an
    # acceptable, documented trade-off.
    warning = _fiscal_period_consistency_warning(result)
    if warning:
        ws.merge_cells(start_row=next_row, start_column=1, end_row=next_row, end_column=end_col)
        warning_cell = ws.cell(next_row, 1, f"⚠ {warning}")
        warning_cell.font = Font(name=FONT_NAME, size=10, bold=True, color="9C6500")
        warning_cell.fill = PatternFill("solid", fgColor=NEUTRAL_GOLD_FILL)
        warning_cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True, indent=1)
        fix_row_height(ws, next_row, 30)
        next_row += 1
    next_row += 1  # spacer

    # The 100%-disposition control headline -- every row is accounted for by
    # construction (see build_qb_dispositions); the control fails only if the
    # disposition ledger and the source population disagree. High-contrast
    # dark text on a pale fill either way (never light text on a mid-tone
    # fill), so the PASS/FAIL state stays legible at a glance.
    headline_row = next_row
    total_rows = int(metrics["QuickBooks Rows"])
    headline_text = f"{total_rows:,} of {total_rows:,} QBO rows accounted for — CONTROL: {metrics['Control Status']}"
    centered_band(
        headline_row, headline_text,
        GREEN_LIGHT if control_ok else RED_LIGHT,
        Font(name=FONT_NAME, size=15, bold=True, color=TEXT if control_ok else "9C0006"),
        height=26,
    )
    next_row = headline_row + 1

    # Secondary: the match rate, explicitly not the headline any more -- a
    # short, de-emphasized single line, left-aligned in its own column so it
    # never needs a spanning band at all.
    rate_row = next_row
    rate_cell = ws.cell(
        rate_row, 1, f"Match rate (secondary measure): {metrics['QuickBooks Match Rate by Row']:.1%}",
    )
    rate_cell.font = Font(name=FONT_NAME, size=9, italic=True, color=SLATE)
    rate_cell.alignment = Alignment(horizontal="left", vertical="center")
    fix_row_height(ws, rate_row, 15)
    next_row = rate_row + 2

    # The KPI ribbon: one column per category (source total, then each final
    # disposition), row count directly above its dollar amount -- a
    # structured grid instead of a single dense equation string. Column A is
    # a row-axis label ("Rows" / "Amount") so the grid reads correctly with
    # or without color.
    def card_value(label: str, kind: str) -> float:
        return metrics[f"Final Disposition - {label} {kind}"]

    ribbon_columns = [("Total QBO Rows", None, SLATE_LIGHT)] + [
        ("JE Support (True Unmatched)" if label == "True Unmatched" else label, label, fill)
        for label, fill in _DISPOSITION_CARD_ORDER
    ]
    label_row = next_row
    count_row = label_row + 1
    amount_row = count_row + 1
    ws.cell(count_row, 1, "Rows").font = Font(name=FONT_NAME, size=9, bold=True, color=SLATE)
    ws.cell(amount_row, 1, "Amount").font = Font(name=FONT_NAME, size=9, bold=True, color=SLATE)
    for row in (count_row, amount_row):
        ws.cell(row, 1).alignment = Alignment(horizontal="left", vertical="center")
        ws.cell(row, 1).fill = PatternFill("solid", fgColor=SLATE_LIGHT)
    for offset, (display_label, disposition_label, fill) in enumerate(ribbon_columns):
        col = offset + 2
        header_cell = ws.cell(label_row, col, display_label)
        header_cell.font = Font(name=FONT_NAME, size=9, bold=True, color=WHITE)
        header_cell.fill = PatternFill("solid", fgColor=NAVY)
        header_cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        header_cell.border = _thin_border()
        if disposition_label is None:
            count_value, amount_value = total_rows, metrics["QuickBooks Source Total"]
        else:
            count_value = int(card_value(disposition_label, "Rows"))
            amount_value = card_value(disposition_label, "Amount")
        count_cell = ws.cell(count_row, col, count_value)
        count_cell.number_format = ACCOUNTING_COUNT_FORMAT
        count_cell.font = Font(name=FONT_NAME_NUMERIC, size=13, bold=True, color=TEXT)
        amount_cell = ws.cell(amount_row, col, amount_value)
        amount_cell.number_format = ACCOUNTING_CURRENCY_FORMAT
        amount_cell.font = Font(name=FONT_NAME_NUMERIC, size=10, bold=True, color=TEXT)
        for cell in (count_cell, amount_cell):
            cell.fill = PatternFill("solid", fgColor=fill)
            cell.alignment = Alignment(horizontal="right", vertical="center")
            cell.border = _thin_border()
    fix_row_height(ws, label_row, 28)
    fix_row_height(ws, count_row, 20)
    fix_row_height(ws, amount_row, 18)
    next_row = amount_row + 2

    # Journal Entry Bridge: the engine's automated total is immutable and
    # never overwritten by a reviewer selection (see the Reviewer Disposition
    # columns on Unresolved Exceptions) -- it is bridged to what actually
    # posts through two purely additive manual adjustments. With no reviewer
    # overrides, both adjustments are zero and Final Approved JE = Engine
    # Proposed JE exactly.
    bridge_title_row = next_row
    unmerged_band(
        bridge_title_row, "JOURNAL ENTRY BRIDGE", SLATE,
        Font(name=FONT_NAME, size=11, bold=True, color=WHITE), height=20,
    )

    released_expr = _review_holds_released_expr()
    excluded_expr = _je_support_manual_exclusions_expr(result)
    bridge_rows = [
        ("Engine Proposed JE (automated TRUE_UNMATCHED total -- immutable)", metrics["Proposed JE Amount"]),
        ("Plus: Review Holds Released to JE (reviewer)", f"={released_expr}"),
        ("Less: Manual JE Exclusions (reviewer, documented reason required)", f"=-({excluded_expr})"),
    ]
    value_col = end_col - 2
    value_col_letter = get_column_letter(value_col)
    bridge_value_cells: list[str] = []
    row = bridge_title_row + 1
    for label, value in bridge_rows:
        for col in range(1, end_col + 1):
            ws.cell(row, col).fill = PatternFill("solid", fgColor=SLATE_LIGHT)
        label_cell = ws.cell(row, 1, label)
        value_cell = ws.cell(row, value_col, value)
        label_cell.font = Font(name=FONT_NAME, size=10, color=TEXT)
        label_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        value_cell.font = Font(name=FONT_NAME_NUMERIC, size=10, color=TEXT)
        value_cell.number_format = ACCOUNTING_CURRENCY_FORMAT
        value_cell.alignment = Alignment(horizontal="right", vertical="center", indent=1)
        value_cell.border = _thin_border()
        bridge_value_cells.append(f"{value_col_letter}{row}")
        fix_row_height(ws, row, 18)
        row += 1

    final_row = row
    for col in range(1, end_col + 1):
        ws.cell(final_row, col).fill = PatternFill("solid", fgColor=GREEN_LIGHT)
    final_label_cell = ws.cell(final_row, 1, "FINAL APPROVED JE")
    final_value_cell = ws.cell(final_row, value_col, "=" + "+".join(bridge_value_cells))
    final_label_cell.font = Font(name=FONT_NAME, size=11, bold=True, color=TEXT)
    final_label_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    final_value_cell.font = Font(name=FONT_NAME_NUMERIC, size=12, bold=True, color=TEXT)
    final_value_cell.number_format = ACCOUNTING_CURRENCY_FORMAT
    final_value_cell.alignment = Alignment(horizontal="right", vertical="center", indent=1)
    final_value_cell.border = _total_border()
    fix_row_height(ws, final_row, 22)
    next_row = final_row + 2

    nav_row = next_row
    ws.merge_cells(start_row=nav_row, start_column=1, end_row=nav_row, end_column=end_col)
    nav_cell = ws.cell(
        nav_row, 1,
        "Review Hold detail and reviewer actions: Unresolved Exceptions (its KPIs, reason code "
        "definitions, and exceptions by fiscal period are in the grouped summary at the top -- "
        "click + beside row 1 to expand it). Every source row: Reconciliation Detail.",
    )
    nav_cell.font = Font(name="Segoe UI", size=9, italic=True, color=SLATE)
    nav_cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    fix_row_height(ws, nav_row, 26)

    for col in range(1, end_col + 1):
        ws.column_dimensions[get_column_letter(col)].width = 18
    ws.column_dimensions["A"].width = 12
    # Wide enough for a six-figure amount without "####". 16.0 is what Excel
    # saves when 15.27 is typed into Column Width (the box excludes padding).
    _pin_column_width(ws, "C", POSTING_SUMMARY_COLUMN_C_WIDTH)
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
