"""The Reconciliation Detail sheet, the paired display frames behind it, and its row hyperlinks."""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..config import (
    AMBER,
    FONT_NAME,
    GREEN_LIGHT,
    INFINIUM_FRIENDLY_HEADERS,
    METHOD_GREY_DARK,
    METHOD_GREY_FILL,
    NAVY,
    NAVY_LIGHT,
    ORANGE,
    RED_LIGHT,
    SLATE_LIGHT,
    TEAL,
    TEAL_LIGHT,
    TEXT,
)
from ..excel_styles import (
    _apply_duplicate_style,
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
from ..matching import QB_ID, ReconciliationResult, REFERENCE_HOLD_SECTION
from ..utils import format_currency
from .tables import (
    _add_plain_data_table,
    _duplicate_source_indexes,
    _fiscal_period_prefix,
    _source_totals,
    _write_dataframe_values,
    RECONCILIATION_DETAIL_SHEET,
    UNRESOLVED_EXCEPTIONS_SHEET,
)
from .raw_data import _resolve_paired_records_bulk


ACCEPTED_PRIOR_PERIOD_LABEL = "Accepted Prior-Period Record"


def _paired_display_frames(result: ReconciliationResult) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    def ordered_union(primary_headers: list[str], historical_headers: list[str]) -> list[str]:
        output = list(primary_headers)
        output.extend(header for header in historical_headers if header not in output)
        return output

    def unique_context_header(base: str, headers: list[str]) -> str:
        candidate = base
        suffix = 2
        while candidate in headers:
            candidate = f"{base} {suffix}"
            suffix += 1
        return candidate

    paired_records = _resolve_paired_records_bulk(result)
    qb_historical_used = any(
        record.get("QB Record Scope") == "Historical" for record in paired_records
    )
    inf_historical_used = any(
        record.get("Infinium Record Scope") == "Historical" for record in paired_records
    )
    qb_historical_headers = (
        list(result.qb_secondary_raw.columns)
        if qb_historical_used and result.qb_secondary_raw is not None else []
    )
    inf_historical_headers = (
        list(result.inf_secondary_raw.columns)
        if inf_historical_used and result.inf_secondary_raw is not None else []
    )
    qb_headers = ordered_union(list(result.qb_raw.columns), qb_historical_headers)
    inf_headers = ordered_union(list(result.inf_raw.columns), inf_historical_headers)
    qb_context_header = unique_context_header("QuickBooks Record Context", qb_headers)
    inf_context_header = unique_context_header("Infinium Record Context", inf_headers)

    qb_work_dict = result.qb_work.to_dict("index") if result.qb_work is not None else {}
    inf_work_dict = result.inf_work.to_dict("index") if result.inf_work is not None else {}
    qb_sec_dict = result.qb_secondary_work.to_dict("index") if result.qb_secondary_work is not None else {}
    inf_sec_dict = result.inf_secondary_work.to_dict("index") if result.inf_secondary_work is not None else {}

    def row_values(
        index: Optional[int],
        scope: Optional[str],
        primary_dict: dict,
        historical_dict: dict,
        headers: list[str],
    ) -> list[Any]:
        if index is None:
            return [None] * len(headers)
        source_dict = historical_dict if scope == "Historical" else primary_dict
        row_data = source_dict.get(index, {})
        return [row_data.get(header) for header in headers]

    qb_rows, inf_rows, match_results = [], [], []
    for record in paired_records:
        qidx, iidx = record["QB Index"], record["Infinium Index"]
        qb_scope = record.get("QB Record Scope")
        inf_scope = record.get("Infinium Record Scope")
        qb_values = row_values(
            qidx, qb_scope, qb_work_dict, qb_sec_dict, qb_headers
        )
        inf_values = row_values(
            iidx, inf_scope, inf_work_dict, inf_sec_dict, inf_headers
        )
        # Record Context is only filled for a deviation from the norm -- a
        # record accepted from the prior-period upload -- so an ordinary
        # primary-upload record stays blank and the exceptions stand out.
        qb_values.append(ACCEPTED_PRIOR_PERIOD_LABEL if qb_scope == "Historical" else None)
        inf_values.append(ACCEPTED_PRIOR_PERIOD_LABEL if inf_scope == "Historical" else None)
        qb_rows.append(qb_values)
        inf_rows.append(inf_values)
        match_results.append(record["Match Result"])
    return (
        pd.DataFrame(qb_rows, columns=qb_headers + [qb_context_header]),
        pd.DataFrame(inf_rows, columns=inf_headers + [inf_context_header]),
        match_results,
    )


# Reconciliation Detail writes exactly one row per result.paired_rows entry,
# in that same order, starting at this row (row 3 is the control strip).
# Other sheets (Unresolved Exceptions) rely on this exact constant to compute
# a hyperlink target without re-deriving this sheet's own layout -- keep them
# in sync.

RECONCILIATION_DETAIL_HEADER_ROW = 4


RECONCILIATION_DETAIL_DATA_ROW = 5


def _qb_id_detail_row_map(result: ReconciliationResult) -> dict[str, int]:
    """Map every QuickBooks Row ID to the row it occupies on Reconciliation
    Detail, so another sheet can link straight to where a row was originally
    listed instead of leaving a reader to search for it by hand."""
    mapping: dict[str, int] = {}
    for offset, record in enumerate(result.paired_rows):
        qidx = record.get("QB Index")
        if qidx is None:
            continue
        scope = record.get("QB Record Scope")
        source = (
            result.qb_secondary_work
            if scope == "Historical" and result.qb_secondary_work is not None
            else result.qb_work
        )
        if source is None or qidx not in source.index:
            continue
        qb_id = source.at[qidx, QB_ID]
        mapping.setdefault(str(qb_id), RECONCILIATION_DETAIL_DATA_ROW + offset)
    return mapping


def _apply_row_id_hyperlink(
    ws, row: int, col: int, target_row: Optional[int], target_sheet: str = RECONCILIATION_DETAIL_SHEET,
) -> None:
    """Turn a cell into a link straight to a specific row on another sheet
    -- if a target couldn't be resolved, leave the cell as plain text
    rather than link to nothing.

    Keeps whatever font color is already on the cell rather than
    replacing it outright -- a duplicate-excluded row's Row ID must stay
    visibly red, not turn hyperlink-blue and lose that signal -- but
    always forces bold plus an underline, so a link is easy to spot at a
    glance regardless of which color/status it's sitting on top of.
    """
    if target_row is None:
        return
    cell = ws.cell(row, col)
    cell.hyperlink = f"#'{target_sheet}'!A{target_row}"
    current = cell.font
    cell.font = Font(
        name=current.name or FONT_NAME,
        size=current.size or 10,
        bold=True,
        color=current.color or NAVY,
        underline="single",
    )


def _find_rows_by_cell_value(ws, target_values: set[str]) -> dict[str, int]:
    """Scan an already-built worksheet for the row each of these exact
    cell values landed on -- used to link back to a sheet whose row
    layout (fiscal-period summary length, KPI rows, stacked sections)
    isn't a simple, safely-reusable formula the way Reconciliation Detail's is.
    """
    found: dict[str, int] = {}
    if not target_values:
        return found
    for row in ws.iter_rows():
        for cell in row:
            value = cell.value
            if value is None:
                continue
            text = str(value)
            if text in target_values and text not in found:
                found[text] = cell.row
    return found


_UNRESOLVED_SHEET_LINKABLE_DISPOSITIONS = frozenset({
    "Retained canonical row",
    "Excluded excess copy",
    "Held for review - excluded from proposed JE pending disposition",
})


def _detail_match_ref_letter(wb: Workbook) -> Optional[str]:
    """Column letter of Match Ref. on the already-built Reconciliation Detail
    sheet, or None if that sheet is absent (links then stay plain text)."""
    if RECONCILIATION_DETAIL_SHEET not in wb.sheetnames:
        return None
    for cell in wb[RECONCILIATION_DETAIL_SHEET][RECONCILIATION_DETAIL_HEADER_ROW]:
        if cell.value == "Match Ref.":
            return get_column_letter(cell.column)
    return None


def _link_reconciled_data_to_unresolved_exceptions(wb: Workbook, result: ReconciliationResult) -> None:
    """The reverse direction of the Row ID links on Unresolved Exceptions:
    for every QuickBooks row shown there as a duplicate or a duplicate
    review-hold item, add a matching link on its Reconciliation Detail row back
    to where it was originally listed as an exception -- so a reviewer
    working from either sheet can always jump to the other.
    """
    if result.duplicate_analysis.empty:
        return
    linkable = result.duplicate_analysis.loc[
        result.duplicate_analysis["Disposition"].isin(_UNRESOLVED_SHEET_LINKABLE_DISPOSITIONS)
    ]
    target_qb_ids = set(linkable["Source Row ID"].astype(str))
    if not target_qb_ids:
        return

    unresolved_ws = wb[UNRESOLVED_EXCEPTIONS_SHEET]
    reconciled_ws = wb[RECONCILIATION_DETAIL_SHEET]
    unresolved_row_by_id = _find_rows_by_cell_value(unresolved_ws, target_qb_ids)
    if not unresolved_row_by_id:
        return

    match_col = next(
        (
            cell.column for cell in reconciled_ws[RECONCILIATION_DETAIL_HEADER_ROW]
            if cell.value == "Match Result"
        ),
        None,
    )
    if match_col is None:
        return
    reconciled_row_by_id = _qb_id_detail_row_map(result)
    for qb_id, unresolved_row in unresolved_row_by_id.items():
        reconciled_row = reconciled_row_by_id.get(qb_id)
        if reconciled_row is None:
            continue
        _apply_row_id_hyperlink(
            reconciled_ws, reconciled_row, match_col, unresolved_row,
            target_sheet=UNRESOLVED_EXCEPTIONS_SHEET,
        )


_MATCHED_SECTIONS = frozenset({"01 Matched", "01 Matched - Historical Clearance"})


_QB_DUPLICATE_EXCLUDED_SECTION = "04 Duplicate QuickBooks"


_QB_REVIEW_HOLD_SECTIONS = frozenset({
    "06 Duplicate Review Hold QuickBooks",
    "08 Reference-Matched Amount Variance Review Hold",
    "09 Fuzzy Match Review Hold",
    "10 Ambiguous Duplicate QuickBooks",
    REFERENCE_HOLD_SECTION,
})


def qb_record_outcomes(result: ReconciliationResult) -> dict[str, int]:
    """Every primary QuickBooks record falls into exactly one outcome -- the
    same four final dispositions the disposition ledger records: reconciled
    (an accepted match, including a prior-period clearance), an excluded
    exact duplicate, a review hold (withheld from the journal entry pending a
    decision), or a true unmatched transaction (the only kind that feeds the
    JE). Counted from the same paired rows the sheet lists, so the control
    strip can never disagree with the rows beneath it."""
    records = reconciled = excluded = review_hold = 0
    for row in result.paired_rows:
        if row.get("QB Index") is None or row.get("QB Record Scope") != "Primary":
            continue
        records += 1
        section = row.get("Section")
        if section in _MATCHED_SECTIONS:
            reconciled += 1
        elif section == _QB_DUPLICATE_EXCLUDED_SECTION:
            excluded += 1
        elif section in _QB_REVIEW_HOLD_SECTIONS:
            review_hold += 1
    return {
        "records": records,
        "reconciled": reconciled,
        "excluded": excluded,
        "review_hold": review_hold,
        "unmatched": records - reconciled - excluded - review_hold,
    }


def _control_strip_text(result: ReconciliationResult) -> str:
    """"661 QuickBooks records | 449 reconciled | 67.9% | 30 review hold |
    178 unmatched | 4 duplicates excluded | JE support: $747,822.02" -- the
    run's outcome in one line, above the detail it summarizes."""
    outcomes = qb_record_outcomes(result)
    rate = outcomes["reconciled"] / outcomes["records"] * 100 if outcomes["records"] else 0.0
    noun = "record" if outcomes["records"] == 1 else "records"
    duplicates = "duplicate" if outcomes["excluded"] == 1 else "duplicates"
    return "   |   ".join((
        f"{outcomes['records']:,} QuickBooks {noun}",
        f"{outcomes['reconciled']:,} reconciled",
        f"{rate:.1f}%",
        f"{outcomes['review_hold']:,} review hold",
        f"{outcomes['unmatched']:,} unmatched",
        f"{outcomes['excluded']:,} {duplicates} excluded",
        f"Engine JE support: {format_currency(result.metrics['Unresolved QuickBooks Amount'])}",
    ))


def _match_ref_link_formula(
    text: str, target_ref_column: str, target_sheet: str = RECONCILIATION_DETAIL_SHEET,
) -> str:
    """A live link from a Referenced Match Ref. cell to the accepted match it
    names on Reconciliation Detail. The row is looked up by reference at
    open time (MATCH over the Match Ref. column), not written as a fixed
    address, so the link still lands on the right relationship after
    Reconciliation Detail is sorted or filtered. A cell naming several
    matches shows all of them and links to the first. Falls back to the
    plain text if the reference cannot be found."""
    shown = str(text).replace('"', '""')
    first = str(text).split(";")[0].strip().replace('"', '""')
    column = target_ref_column
    return (
        f'=IFERROR(HYPERLINK("#\'{target_sheet}\'!{column}"&MATCH("{first}",'
        f"'{target_sheet}'!${column}:${column},0),"
        f'"{shown}"),"{shown}")'
    )


def _style_match_ref_link(cell, text: str, target_ref_column: str) -> None:
    cell.value = _match_ref_link_formula(text, target_ref_column)
    cell.font = Font(name=FONT_NAME, size=10, bold=True, underline="single", color=NAVY)
    cell.alignment = Alignment(horizontal="center", vertical="center")


def _friendly_infinium_headers(ws, header_row: int, start_col: int, raw_headers: list[str]) -> None:
    """Show Infinium's plain field names (Period, Customer No., Amount ...) in
    place of its system codes. The original code stays one hover away in a
    cell comment; the Raw Data sheet keeps every code exactly as uploaded."""
    for offset, raw_header in enumerate(raw_headers):
        friendly = INFINIUM_FRIENDLY_HEADERS.get(str(raw_header).strip().upper())
        if not friendly:
            continue
        cell = ws.cell(header_row, start_col + offset)
        cell.value = friendly
        comment = Comment(f"Infinium field code: {raw_header}", "Sales Reconciliation")
        comment.width, comment.height = 190, 44
        cell.comment = comment


def build_reconciliation_detail_sheet(wb: Workbook, result: ReconciliationResult) -> None:
    ws = wb.create_sheet(RECONCILIATION_DETAIL_SHEET)
    qb_display, inf_display, match_results = _paired_display_frames(result)
    qb_headers = list(qb_display.columns)
    inf_headers = list(inf_display.columns)
    qb_start = 1
    # Match panel: Match Ref. sits immediately before Match Result; the
    # Referenced Match Ref. pointer (an exception naming a match that already
    # consumed a record) follows it.
    ref_col = len(qb_headers) + 1
    match_col = ref_col + 1
    referenced_col = match_col + 1
    inf_start = referenced_col + 1
    qb_end = len(qb_headers)
    inf_end = inf_start + len(inf_headers) - 1
    strip_row = RECONCILIATION_DETAIL_HEADER_ROW - 1
    header_row, data_row = RECONCILIATION_DETAIL_HEADER_ROW, RECONCILIATION_DETAIL_DATA_ROW
    final_data_row = data_row + len(match_results) - 1
    ref_letter = get_column_letter(ref_col)

    _write_title_band(
        ws, 1, qb_start, qb_end, f"{_fiscal_period_prefix(result)} | QUICKBOOKS | RECONCILIATION DETAIL", NAVY,
    )
    _write_title_band(ws, 1, ref_col, referenced_col, "MATCH RESULT", METHOD_GREY_DARK)
    _write_title_band(ws, 1, inf_start, inf_end, "INFINIUM | RECONCILIATION DETAIL", TEAL)
    _write_caption_band(
        ws, 2, qb_start, qb_end,
        "Every primary QuickBooks record appears once, reconciled or not. Any accepted QuickBooks prior-period match is displayed on this side and labeled in Record Context.",
        NAVY,
    )
    _write_caption_band(ws, 2, ref_col, referenced_col, "Matching Methodology", METHOD_GREY_DARK)
    _write_caption_band(
        ws, 2, inf_start, inf_end,
        "Every primary Infinium record appears once. Accepted prior-period matches are displayed; unused historical rows are excluded.",
        TEAL,
    )

    # Control strip: the run's outcome in one line, plus the control result.
    strip_cell = ws.cell(strip_row, qb_start, _control_strip_text(result))
    ws.merge_cells(start_row=strip_row, start_column=qb_start, end_row=strip_row, end_column=match_col)
    for col in range(qb_start, match_col + 1):
        ws.cell(strip_row, col).fill = PatternFill("solid", fgColor=SLATE_LIGHT)
        ws.cell(strip_row, col).border = _thin_border()
    strip_cell.font = Font(name=FONT_NAME, size=10, bold=True, color=NAVY)
    strip_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1, shrink_to_fit=True)
    control_passed = result.metrics["Control Status"] == "PASS"
    control_cell = ws.cell(strip_row, referenced_col, f"Control: {result.metrics['Control Status']}")
    control_cell.fill = PatternFill("solid", fgColor=GREEN_LIGHT if control_passed else RED_LIGHT)
    control_cell.font = Font(name=FONT_NAME, size=10, bold=True, color=TEXT)
    control_cell.border = _thin_border()
    control_cell.alignment = Alignment(horizontal="center", vertical="center", shrink_to_fit=True)
    fix_row_height(ws, strip_row, 22)

    _write_dataframe_values(ws, qb_display, header_row, qb_start)
    ws.cell(header_row, ref_col, "Match Ref.")
    ws.cell(header_row, match_col, "Match Result")
    ws.cell(header_row, referenced_col, "Referenced Match Ref.")
    resolved_records = _resolve_paired_records_bulk(result)
    for offset, (value, paired) in enumerate(zip(match_results, resolved_records), 1):
        ws.cell(header_row + offset, ref_col, paired.get("Match Ref.") or None)
        ws.cell(header_row + offset, match_col, value)
        ws.cell(header_row + offset, referenced_col, paired.get("Referenced Match Ref.") or None)
    _write_dataframe_values(ws, inf_display, header_row, inf_start)
    qb_amount_cols = {result.qb_mapping["amount"]}
    qb_quantity_cols = {result.qb_mapping.get("quantity") or ""}
    inf_amount_cols = {result.inf_mapping["amount"]}
    # Alignment and number formats key off the original headers (the amount
    # column is literally "OHTOTA"); the friendly names are painted on last.
    _format_header(ws, header_row, qb_start, qb_end, NAVY, qb_headers, qb_amount_cols, qb_quantity_cols)
    _format_header(ws, header_row, ref_col, referenced_col, METHOD_GREY_DARK)
    _format_header(ws, header_row, inf_start, inf_end, TEAL, inf_headers, inf_amount_cols)
    for code_col in (ref_col, referenced_col):
        ws.cell(header_row, code_col).alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    _format_body_block(ws, data_row, final_data_row, qb_start, qb_end, NAVY_LIGHT)
    _format_body_block(ws, data_row, final_data_row, ref_col, referenced_col, METHOD_GREY_FILL)
    _format_body_block(ws, data_row, final_data_row, inf_start, inf_end, TEAL_LIGHT)

    duplicate_qb_rows = _duplicate_source_indexes(result, "QuickBooks")
    duplicate_inf_rows = _duplicate_source_indexes(result, "Infinium")
    for offset, record in enumerate(resolved_records):
        row = data_row + offset
        status = str(record["Section"])
        if status == "02 Unmatched QuickBooks":
            for col in range(qb_start, referenced_col + 1):
                ws.cell(row, col).fill = PatternFill("solid", fgColor=AMBER)
        elif status == "03 Unmatched Infinium":
            for col in range(ref_col, inf_end + 1):
                ws.cell(row, col).fill = PatternFill("solid", fgColor=ORANGE)
        qidx = record["QB Index"]
        iidx = record["Infinium Index"]
        if (
            record.get("QB Record Scope") == "Primary"
            and qidx is not None
            and int(qidx) in duplicate_qb_rows
        ):
            _apply_duplicate_style(ws, row, qb_start, qb_end)
        if (
            record.get("Infinium Record Scope") == "Primary"
            and iidx is not None
            and int(iidx) in duplicate_inf_rows
        ):
            _apply_duplicate_style(ws, row, inf_start, inf_end)
        if record.get("QB Record Scope") == "Historical":
            ws.cell(row, qb_end).font = Font(
                name=FONT_NAME, size=10, bold=True, color=NAVY
            )
        if record.get("Infinium Record Scope") == "Historical":
            ws.cell(row, inf_end).font = Font(
                name=FONT_NAME, size=10, bold=True, color=TEAL
            )
        ws.cell(row, match_col).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.cell(row, ref_col).alignment = Alignment(horizontal="center", vertical="center")
        pointer = record.get("Referenced Match Ref.")
        if pointer:
            # An exception naming an accepted match links straight to it.
            _style_match_ref_link(ws.cell(row, referenced_col), pointer, ref_letter)
        else:
            ws.cell(row, referenced_col).alignment = Alignment(horizontal="center", vertical="center")

    total_row = final_data_row + 1
    _write_total_row(ws, total_row, qb_start, qb_end,
                     _source_totals(result.qb_raw, result.qb_mapping), qb_headers, "RECONCILIATION TOTAL")
    _write_total_row(ws, total_row, inf_start, inf_end,
                     _source_totals(result.inf_raw, result.inf_mapping), inf_headers, "RECONCILIATION TOTAL")
    ws.cell(total_row, match_col, f"Control: {result.metrics['Control Status']}")
    ws.cell(total_row, match_col).fill = PatternFill("solid", fgColor=GREEN_LIGHT if control_passed else RED_LIGHT)
    ws.cell(total_row, match_col).font = Font(name=FONT_NAME, size=10, bold=True, color=TEXT)
    ws.cell(total_row, match_col).border = _total_border()
    ws.cell(total_row, match_col).alignment = Alignment(horizontal="center", vertical="center")
    _apply_number_formats(ws, qb_headers, data_row, total_row, qb_start,
                          qb_amount_cols, qb_quantity_cols)
    _apply_number_formats(ws, inf_headers, data_row, total_row, inf_start,
                          inf_amount_cols, set())
    _friendly_infinium_headers(ws, header_row, inf_start, inf_headers)
    _set_widths(ws, qb_start, qb_end, header_row, total_row)
    _set_widths(ws, inf_start, inf_end, header_row, total_row)
    ws.column_dimensions[get_column_letter(match_col)].width = 43
    # Wide enough for the full heading and a reference: the workbook-wide
    # autofit must not stretch (or, for the link formulas, misread) them.
    _pin_column_width(ws, ref_letter, 12)
    _pin_column_width(ws, get_column_letter(referenced_col), 22)
    ws.freeze_panes = f"{get_column_letter(inf_start)}{data_row}"
    # Three genuine Excel Tables (ListObjects) -- one per visual block --
    # instead of one plain sheet-wide AutoFilter, so a screen reader
    # announces each block's own column headers while navigating its rows.
    # Each table carries its own filter dropdowns, so every column stays
    # filterable exactly as before.
    _add_plain_data_table(
        ws, table_name="ReconciliationDetailQuickBooks", start_col=qb_start, end_col=qb_end,
        header_row=header_row, last_data_row=final_data_row,
    )
    _add_plain_data_table(
        ws, table_name="ReconciliationDetailMatchInfo", start_col=ref_col, end_col=referenced_col,
        header_row=header_row, last_data_row=final_data_row,
    )
    _add_plain_data_table(
        ws, table_name="ReconciliationDetailInfinium", start_col=inf_start, end_col=inf_end,
        header_row=header_row, last_data_row=final_data_row,
    )
    ws.print_title_rows = f"1:{header_row}"
    _prepare_sheet(ws)


paired_display_frames = _paired_display_frames
