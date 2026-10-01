"""The simplified legacy-format workbook an accountant can compare with older runs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..config import (
    CENTRAL_TIMEZONE,
    DATE_NUMBER_FORMAT,
    FONT_NAME,
    INFINIUM_FRIENDLY_HEADERS,
    LEGACY_BODY_TEXT,
    LEGACY_EXCLUDED_FILL,
    LEGACY_MATCHED_FILL,
    LEGACY_METHOD_FILL,
    LEGACY_NO_PAIR_FILL,
    LEGACY_REVIEW_FILL,
    METHOD_GREY_DARK,
    NAVY,
    NAVY_LIGHT,
    SLATE,
    SLATE_LIGHT,
    TEAL,
)
from ..duplicates import DUPLICATE_BASIS_INVOICE_ONLY, DUPLICATE_BASIS_PO_ONLY, NORM_PO
from ..excel_styles import (
    _apply_default_alignment,
    _apply_legacy_status_cell,
    _apply_legacy_status_fill,
    _apply_number_formats,
    _format_body_block,
    _format_header,
    _prepare_sheet,
    _set_widths,
    _standardize_column_widths,
    _thin_border,
    _total_border,
    _write_caption_band,
    _write_title_band,
    _write_total_row,
)
from ..matching import (
    build_fiscal_exception_summary,
    describe_match_references,
    parse_fiscal_period,
    ReconciliationResult,
    REFERENCE_HOLD_SECTION,
    validate_match_references,
)
from ..utils import excel_safe
from .tables import _source_totals, _write_dataframe_values
from .finishing import _apply_workbook_run_metadata, _save_workbook_bytes
from .summary_sheets import build_aggregates_sheet


_LEGACY_MATCHED_SECTIONS = {
    "01 Matched",
    "01 Matched - Historical Clearance",
}


_LEGACY_QB_DUPLICATE_SECTIONS = {"04 Duplicate QuickBooks"}


_LEGACY_INF_DUPLICATE_SECTIONS = {"05 Duplicate Infinium"}


_LEGACY_DUPLICATE_SECTIONS = _LEGACY_QB_DUPLICATE_SECTIONS | _LEGACY_INF_DUPLICATE_SECTIONS


_LEGACY_INF_UNMATCHED_SECTION = "03 Unmatched Infinium"


def _legacy_section_label(section: str) -> str:
    """Strip the sort-order prefix (e.g. "04 ") from a Section value for a
    plain, management-facing exception type label."""
    prefix, _, remainder = str(section).partition(" ")
    return remainder if prefix.isdigit() and remainder else str(section)


def _legacy_row_values(
    index: Optional[int],
    scope: Optional[str],
    primary_frame: Optional[pd.DataFrame],
    historical_frame: Optional[pd.DataFrame],
    headers: list[str],
) -> list[Any]:
    if index is None:
        return [None] * len(headers)
    source = historical_frame if scope == "Historical" and historical_frame is not None else primary_frame
    if source is None or index not in source.index:
        return [None] * len(headers)
    row = source.loc[index]
    return [row.get(header) for header in headers]


def _legacy_norm_po_sort_key(index: Optional[int], frame: pd.DataFrame) -> tuple[bool, str]:
    """Sort key that puts a blank/unavailable normalized PO last."""
    value = ""
    if index is not None and NORM_PO in frame.columns and index in frame.index:
        value = str(frame.at[index, NORM_PO] or "")
    return (value == "", value)


# Fixed, type-appropriate widths for a raw source frame's mapped fields --
# same idea as _apply_number_formats' amount/quantity/date treatment, but
# for column width. Keyed by role rather than header text so it works
# whatever the export happens to call these columns (e.g. Infinium's
# amount field is literally "OHTOTA", which doesn't textually resemble
# "amount" at all).

_LEGACY_ROLE_WIDTHS = {"period": 10, "invoice": 16, "po": 18, "amount": 15, "quantity": 12}


def _legacy_fixed_widths(mapping: dict[str, Optional[str]]) -> dict[str, float]:
    widths: dict[str, float] = {}
    for role, width in _LEGACY_ROLE_WIDTHS.items():
        column = mapping.get(role)
        if column:
            widths[column] = width
    return widths


def _standardize_legacy_widths(ws, headers: list[str], start_col: int, mapping: dict[str, Optional[str]]) -> None:
    """Apply role-based fixed widths for mapped fields, plus a generic
    date-column width for any remaining header that looks like a date --
    the one column type with no dedicated mapping key of its own."""
    _standardize_column_widths(ws, headers, start_col, _legacy_fixed_widths(mapping))
    for offset, header in enumerate(headers):
        header_upper = str(header).upper()
        if "DATE" in header_upper or "TIMESTAMP" in header_upper:
            ws.column_dimensions[get_column_letter(start_col + offset)].width = 13


def _legacy_infinium_display_headers(headers: list[str]) -> list[str]:
    return [INFINIUM_FRIENDLY_HEADERS.get(str(header).strip().upper(), header) for header in headers]


def _legacy_matched_label(match_result: str) -> str:
    """"Unique Match: PO + Amount", "Group Match: Invoice + Amount", etc. --
    only how the match was made, never the confidence tier. A vendor-alias
    match is a PO-field + amount match, and a historical (prior-period)
    clearance uses the same underlying rule after its "... | " prefix."""
    text = str(match_result).split("|")[-1].strip()
    is_group = "Grouped" in text or "group-level" in str(match_result)
    if "PO + Invoice" in text:
        keys = "PO + Invoice + Amount"
    elif text.startswith("Invoice +"):
        keys = "Invoice + Amount"
    else:
        keys = "PO + Amount"
    return f"{'Group' if is_group else 'Unique'} Match: {keys}"


def _legacy_reference_label(record: dict) -> Optional[str]:
    """Legacy wording for an exception that points at an accepted match --
    built from the canonical reference stored on the paired row, never
    re-derived here, so it always names a match that exists."""
    references = _split_cell_references(record.get("Referenced Match Ref."))
    if not references:
        return None
    basis = record.get("Reference Basis")
    if basis == "Group":
        return f"Review: Candidate Belongs to {describe_match_references(references, group=True)}"
    if basis in ("PO", "Invoice"):
        return f"Review: {basis} Already Used by {describe_match_references(references)}"
    if basis == "Record":
        return f"Infinium Record Already Assigned to {describe_match_references(references)}"
    if record.get("Section") in _LEGACY_DUPLICATE_SECTIONS:
        return f"Exact Duplicate of {describe_match_references(references)} - Excluded"
    return f"Potential Duplicate of {describe_match_references(references)}"


def _split_cell_references(text: Any) -> list[str]:
    return [piece.strip() for piece in str(text or "").split(";") if piece.strip()]


def _legacy_final_disposition(record: dict) -> str:
    """The same four top-level dispositions shown everywhere else in the
    workbook (MATCHED / TRUE UNMATCHED / REVIEW HOLD / DUPLICATE EXCLUDED),
    derived straight from the paired row's Section -- so this ledger and the
    Unresolved Exceptions / Reconciliation Detail sheets can never disagree.
    Blank for an Infinium-only row, which carries no QuickBooks disposition."""
    section = str(record.get("Section", ""))
    if section in _LEGACY_MATCHED_SECTIONS:
        return "MATCHED"
    if record.get("QB Index") is None:
        return ""
    if section in _LEGACY_QB_DUPLICATE_SECTIONS:
        return "DUPLICATE EXCLUDED"
    if section == "02 Unmatched QuickBooks":
        return "TRUE UNMATCHED"
    return "REVIEW HOLD"


_LEGACY_REFERENCE_HOLD_LABELS = {
    "REVIEW_HOLD_AMOUNT_VARIANCE": "Amount Differs: Same PO/Invoice",
    "REVIEW_HOLD_EXACT_CANDIDATE_NOT_UNIQUE": "Review: Exact Candidate Not Unique",
    "REVIEW_HOLD_MULTIPLE_CANDIDATES": "Potential Duplicate: Multiple Infinium Candidates",
    "REVIEW_HOLD_INVALID_AMOUNT": "Review: Invalid Amount",
    "REVIEW_HOLD_CANDIDATE_INVALID_AMOUNT": "Review: Candidate Amount Invalid",
    "REVIEW_HOLD_PO_ALREADY_REPRESENTED": "Review: PO Already Represented",
}


def _legacy_match_method_label(record: dict) -> str:
    """The Legacy Reconciliation "Match Result" text: just how the match
    was made (or why there isn't one). Confidence tiers stay on the primary
    workpaper and analytics package -- the accountant's legacy view is a
    read-on-sight summary, and the row's fill already carries the status."""
    section = str(record.get("Section", ""))
    match_result = str(record.get("Match Result", ""))
    if section in _LEGACY_MATCHED_SECTIONS:
        return _legacy_matched_label(match_result)
    if section == "09 Fuzzy Match Review Hold":
        # A fuzzy match is always held for review -- never counted as
        # reconciled -- so its label lives on the review path, not the
        # matched one, even though the wording is unchanged.
        return "Possible Match: Similar PO + Amount"
    pointer = _legacy_reference_label(record)
    if pointer:
        return pointer
    if section == "02 Unmatched QuickBooks":
        return "No Matching Infinium Records"
    if section == "03 Unmatched Infinium":
        return "No Matching QuickBooks Records"
    if section in _LEGACY_DUPLICATE_SECTIONS:
        return "Duplicate: Excess Copy Excluded"
    if section in {"06 Duplicate Review Hold QuickBooks", "07 Duplicate Review Hold Infinium"}:
        basis = record.get("Duplicate Basis")
        if basis == DUPLICATE_BASIS_PO_ONLY:
            return "Potential Duplicate: PO + Amount"
        if basis == DUPLICATE_BASIS_INVOICE_ONLY:
            return "Potential Duplicate: Invoice + Amount"
        return "Potential Duplicate: PO + Invoice + Amount"
    if section == "08 Reference-Matched Amount Variance Review Hold":
        return "Amount Differs: Same PO/Invoice"
    if section == "10 Ambiguous Duplicate QuickBooks":
        return "Potential Duplicate: Multiple Infinium Candidates"
    if section == REFERENCE_HOLD_SECTION:
        return _LEGACY_REFERENCE_HOLD_LABELS.get(str(record.get("Reason Code")), match_result)
    return match_result


def _legacy_row_needs_attention(section: str) -> bool:
    """A review row (gold) or a fuzzy possible match is the only kind of row
    whose status text is bolded; a clean match and an already-decided
    excluded duplicate are informational."""
    return (
        section not in _LEGACY_MATCHED_SECTIONS and section not in _LEGACY_DUPLICATE_SECTIONS
    ) or section == "09 Fuzzy Match Review Hold"


def _write_legacy_legend(ws, row: int, start_col: int) -> None:
    """A compact color key just under the introductory note: three adjacent
    chips, each filled with the exact row tint it explains and carrying its
    own label, so the key and the rows can never disagree. (A colored "■"
    glyph would be invisible for the paler tints.) Text shrinks to fit
    whatever width its column happens to have."""
    chip_border_side = Side(style="thin", color="BFBFBF")
    chips = [
        (LEGACY_MATCHED_FILL, "Reconciled"),
        (LEGACY_REVIEW_FILL, "Review required"),
        (LEGACY_EXCLUDED_FILL, "Excluded duplicate"),
        (LEGACY_NO_PAIR_FILL, "No paired record"),
    ]
    for offset, (fill_color, label) in enumerate(chips):
        cell = ws.cell(row, start_col + offset, label)
        cell.fill = PatternFill("solid", fgColor=fill_color)
        cell.font = Font(name=FONT_NAME, size=9, color=LEGACY_BODY_TEXT)
        cell.alignment = Alignment(horizontal="center", vertical="center", shrink_to_fit=True)
        cell.border = Border(
            left=chip_border_side, right=chip_border_side, top=chip_border_side, bottom=chip_border_side,
        )
    ws.row_dimensions[row].height = 18


def _legacy_generated_stamp(run_timestamp: datetime) -> str:
    """"09/19/2026 3:21 PM CDT" -- MM/DD/YYYY and a 12-hour clock, in
    Central time, matching how every date on the legacy sheets displays."""
    local = run_timestamp.astimezone(CENTRAL_TIMEZONE)
    hour = local.strftime("%I").lstrip("0") or "12"
    return f"{local:%m/%d/%Y} {hour}:{local:%M} {local:%p} {local:%Z}"


_LEGACY_DATE_INPUT_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%Y-%m-%d %H:%M:%S", "%m-%d-%Y")


def _legacy_parse_date(value: Any) -> Optional[datetime]:
    """A source date as a real date: an existing datetime/date passes
    through, and text like "1/09/2026" or "2026-03-03" is parsed month-first
    (the export's own convention). Anything else is not treated as a date."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for pattern in _LEGACY_DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _legacy_ensure_headers_fit(ws, headers: list[str], start_col: int) -> None:
    """Widen any column too narrow for its own bold heading plus the
    autofilter button Excel draws inside it -- otherwise a short-valued
    column such as an Infinium "Customer No" (five-digit values) is sized
    to its data and its heading is clipped to "Customer N"."""
    for offset, header in enumerate(headers):
        letter = get_column_letter(start_col + offset)
        needed = len(str(header)) + 5
        current = ws.column_dimensions[letter].width or 0
        if current < needed:
            ws.column_dimensions[letter].width = needed


def _legacy_standardize_dates(
    ws, headers: list[str], start_col: int, first_row: int, last_row: int,
) -> None:
    """Every date-column cell on a legacy sheet becomes a real date shown as
    MM/DD/YYYY. QuickBooks delivers some dates as real dates and others as
    text, which otherwise render as "2026-03-03" beside "08/17/2026"; a
    text date is converted, and all date cells are centered alike."""
    for offset, header in enumerate(headers):
        if "DATE" not in str(header).upper():
            continue
        for row in range(first_row, last_row + 1):
            cell = ws.cell(row, start_col + offset)
            parsed = _legacy_parse_date(cell.value)
            if parsed is None:
                continue
            cell.value = parsed
            cell.number_format = DATE_NUMBER_FORMAT
            cell.alignment = Alignment(horizontal="center", vertical="center")


def build_legacy_reconciliation_sheet(wb: Workbook, result: ReconciliationResult) -> None:
    """A simplified, side-by-side QuickBooks/Infinium sheet styled after the
    accountant's original hand-built workbook: every QuickBooks row (sorted
    by normalized PO), colored by outcome, with its matched Infinium row
    riding along on the same line when one exists and blank when it
    doesn't. QuickBooks exceptions repeat on their own Exceptions sheet;
    Infinium is only shown here (never on Exceptions) and only for rows
    that actually matter on this sheet -- a confirmed match, an Infinium
    duplicate (any period), or an unmatched Infinium row from the current
    selected period. Older-period Infinium noise with no QuickBooks tie
    is intentionally left out.
    """
    ws = wb.active
    ws.title = "Legacy Reconciliation"
    qb_headers = list(result.qb_raw.columns)
    inf_headers = list(result.inf_raw.columns)
    qb_start = 1
    # Final Disposition (the same four top-level outcomes shown everywhere
    # else) leads the panel, followed by Match Ref., Match Result, and
    # Referenced Match Ref. -- prioritized in that order so the outcome a
    # reviewer needs is the first thing to the right of the QuickBooks block,
    # ahead of the how-it-resolved and why-it-points-elsewhere detail.
    disposition_col = len(qb_headers) + 1
    ref_col = disposition_col + 1
    method_col = ref_col + 1
    referenced_col = method_col + 1
    inf_start = referenced_col + 1
    qb_end = len(qb_headers)
    inf_end = inf_start + len(inf_headers) - 1
    # Row 3 holds the color legend, directly under the introductory note.
    legend_row, header_row, data_row = 3, 4, 5

    default_year = int(result.metadata.get("fiscal_year") or result.run_timestamp.year)
    selected_period = result.metadata.get("fiscal_period")
    inf_period_col = result.inf_mapping.get("period")

    def inf_row_period(record: dict) -> Any:
        iidx = record.get("Infinium Index")
        if iidx is not None and inf_period_col and iidx in result.inf_work.index:
            period, _ = parse_fiscal_period(result.inf_work.at[iidx, inf_period_col], default_year)
            return period
        return None

    qb_rows = [record for record in result.paired_rows if record.get("QB Index") is not None]
    inf_only_rows = [
        record for record in result.paired_rows
        if record.get("QB Index") is None
        and record.get("Infinium Index") is not None
        and (
            record.get("Section") in _LEGACY_INF_DUPLICATE_SECTIONS
            or (
                record.get("Section") == _LEGACY_INF_UNMATCHED_SECTION
                and selected_period is not None
                and inf_row_period(record) == int(selected_period)
            )
        )
    ]
    qb_rows.sort(key=lambda record: _legacy_norm_po_sort_key(record.get("QB Index"), result.qb_work))
    inf_only_rows.sort(key=lambda record: _legacy_norm_po_sort_key(record.get("Infinium Index"), result.inf_work))
    all_rows = qb_rows + inf_only_rows
    final_data_row = data_row + max(len(all_rows), 1) - 1
    matched_count = sum(1 for record in all_rows if record.get("Section") in _LEGACY_MATCHED_SECTIONS)
    # The red-row sentence in the note is only meaningful if a red row exists.
    has_excluded_duplicates = any(record.get("Section") in _LEGACY_DUPLICATE_SECTIONS for record in all_rows)

    _write_title_band(ws, 1, qb_start, qb_end, "QUICKBOOKS | SORTED BY PO", NAVY)
    _write_title_band(ws, 1, disposition_col, referenced_col, "MATCH RESULT", SLATE)
    _write_title_band(ws, 1, inf_start, inf_end, "INFINIUM", TEAL)
    _write_caption_band(
        ws, 2, qb_start, qb_end,
        f"All {len(qb_rows):,} QuickBooks records accounted for -- {matched_count:,} matched "
        f"({(matched_count / len(qb_rows) * 100) if qb_rows else 0:.1f}%). "
        f"{'Gold rows require review; red rows are excluded duplicates.' if has_excluded_duplicates else 'Gold rows require review.'} "
        f"See Exceptions for details. Generated {_legacy_generated_stamp(result.run_timestamp)}.",
        NAVY,
    )
    _write_caption_band(
        ws, 2, disposition_col, referenced_col,
        "Final Disposition (MATCHED / TRUE UNMATCHED / REVIEW HOLD / DUPLICATE EXCLUDED), then how each "
        "row resolved, or why it did not.",
        SLATE,
    )
    _write_caption_band(
        ws, 2, inf_start, inf_end,
        "Blank unless matched. Unmatched Infinium rows are shown only for the currently "
        "selected fiscal period; an Infinium duplicate is shown for any period.",
        TEAL,
    )

    for offset, record in enumerate(all_rows):
        row = data_row + offset
        qb_values = _legacy_row_values(
            record.get("QB Index"), record.get("QB Record Scope"),
            result.qb_work, result.qb_secondary_work, qb_headers,
        )
        inf_values = _legacy_row_values(
            record.get("Infinium Index"), record.get("Infinium Record Scope"),
            result.inf_work, result.inf_secondary_work, inf_headers,
        )
        for col_offset, value in enumerate(qb_values):
            ws.cell(row, qb_start + col_offset, excel_safe(value))
        for col_offset, value in enumerate(inf_values):
            ws.cell(row, inf_start + col_offset, excel_safe(value))
        ws.cell(row, disposition_col, _legacy_final_disposition(record) or None)
        ws.cell(row, ref_col, record.get("Match Ref.") or None)
        ws.cell(row, method_col, _legacy_match_method_label(record))
        ws.cell(row, referenced_col, record.get("Referenced Match Ref.") or None)

    ws.cell(header_row, disposition_col, "Final Disposition")
    ws.cell(header_row, ref_col, "Match Ref.")
    ws.cell(header_row, method_col, "Match Result")
    ws.cell(header_row, referenced_col, "Referenced Match Ref.")
    _write_legacy_legend(ws, legend_row, qb_start)
    _write_dataframe_values(ws, pd.DataFrame(columns=qb_headers), header_row, qb_start)
    _write_dataframe_values(
        ws, pd.DataFrame(columns=_legacy_infinium_display_headers(inf_headers)), header_row, inf_start,
    )
    _format_header(ws, header_row, qb_start, qb_end, NAVY)
    _format_header(ws, header_row, disposition_col, referenced_col, SLATE)
    _format_header(ws, header_row, inf_start, inf_end, TEAL)

    blank_side_panels: list[tuple[int, int, int]] = []
    for offset, record in enumerate(all_rows):
        row = data_row + offset
        section = record.get("Section", "")
        # Alignment/border first, uniform across the whole row regardless
        # of outcome, so every cell has a consistent look; the color style
        # applied next only ever touches fill/font, never alignment.
        _apply_default_alignment(ws, row, qb_start, inf_end)
        if section in _LEGACY_MATCHED_SECTIONS:
            status_fill = LEGACY_MATCHED_FILL
        elif section in _LEGACY_DUPLICATE_SECTIONS:
            status_fill = LEGACY_EXCLUDED_FILL
        else:
            status_fill = LEGACY_REVIEW_FILL
        # A side with no record in its own dataset (no paired Infinium row,
        # or an Infinium-only row with no QuickBooks row) stays blank in a
        # near-white gray, so it reads as "nothing here" rather than as a
        # second, empty status band.
        has_qb = record.get("QB Index") is not None
        has_inf = record.get("Infinium Index") is not None
        _apply_legacy_status_fill(ws, row, qb_start, qb_end, status_fill if has_qb else LEGACY_NO_PAIR_FILL)
        _apply_legacy_status_fill(ws, row, inf_start, inf_end, status_fill if has_inf else LEGACY_NO_PAIR_FILL)
        if not has_qb:
            blank_side_panels.append((row, qb_start, qb_end))
        if not has_inf:
            blank_side_panels.append((row, inf_start, inf_end))
        needs_attention = _legacy_row_needs_attention(section)
        _apply_legacy_status_cell(ws, row, disposition_col, LEGACY_METHOD_FILL, needs_attention)
        _apply_legacy_status_cell(ws, row, ref_col, LEGACY_METHOD_FILL, False)
        _apply_legacy_status_cell(ws, row, method_col, LEGACY_METHOD_FILL, needs_attention)
        _apply_legacy_status_cell(ws, row, referenced_col, LEGACY_METHOD_FILL, False)
        ws.cell(row, method_col).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        for code_col in (disposition_col, ref_col, referenced_col):
            ws.cell(row, code_col).alignment = Alignment(horizontal="center", vertical="center")
        for panel_col in (disposition_col, ref_col, method_col, referenced_col):
            ws.cell(row, panel_col).border = _thin_border()
    if not all_rows:
        _apply_default_alignment(ws, data_row, qb_start, inf_end)
        for panel_col in (disposition_col, ref_col, method_col, referenced_col):
            _apply_legacy_status_cell(ws, data_row, panel_col, LEGACY_METHOD_FILL, False)

    total_row = final_data_row + 1
    qb_display = pd.DataFrame(
        [_legacy_row_values(r.get("QB Index"), r.get("QB Record Scope"), result.qb_work, result.qb_secondary_work, qb_headers) for r in qb_rows],
        columns=qb_headers,
    )
    inf_display = pd.DataFrame(
        [_legacy_row_values(r.get("Infinium Index"), r.get("Infinium Record Scope"), result.inf_work, result.inf_secondary_work, inf_headers) for r in all_rows],
        columns=inf_headers,
    )
    _write_total_row(ws, total_row, qb_start, qb_end,
                     _source_totals(qb_display, result.qb_mapping), qb_headers, "QUICKBOOKS TOTAL")
    _write_total_row(ws, total_row, inf_start, inf_end,
                     _source_totals(inf_display, result.inf_mapping), inf_headers, "INFINIUM TOTAL (SHOWN)")
    for panel_col in (disposition_col, ref_col, method_col, referenced_col):
        ws.cell(total_row, panel_col).fill = PatternFill("solid", fgColor=SLATE_LIGHT)
        ws.cell(total_row, panel_col).border = _total_border()
    _apply_number_formats(ws, qb_headers, data_row, total_row, qb_start,
                          {result.qb_mapping["amount"]}, {result.qb_mapping.get("quantity") or ""})
    _apply_number_formats(ws, inf_headers, data_row, total_row, inf_start,
                          {result.inf_mapping["amount"]}, set())
    _set_widths(ws, qb_start, qb_end, header_row, total_row, maximum=40)
    _set_widths(ws, inf_start, inf_end, header_row, total_row, maximum=40)
    _standardize_legacy_widths(ws, qb_headers, qb_start, result.qb_mapping)
    _standardize_legacy_widths(ws, inf_headers, inf_start, result.inf_mapping)
    _legacy_ensure_headers_fit(ws, qb_headers, qb_start)
    _legacy_ensure_headers_fit(ws, _legacy_infinium_display_headers(inf_headers), inf_start)
    ws.column_dimensions[get_column_letter(method_col)].width = 46
    # Narrow, but wide enough for a reference and the full (wrapping) heading.
    ws.column_dimensions[get_column_letter(disposition_col)].width = 18
    ws.column_dimensions[get_column_letter(ref_col)].width = 12
    ws.column_dimensions[get_column_letter(referenced_col)].width = 16
    _legacy_standardize_dates(ws, qb_headers, qb_start, data_row, final_data_row)
    _legacy_standardize_dates(ws, _legacy_infinium_display_headers(inf_headers), inf_start, data_row, final_data_row)
    # Each blank side reads as one quiet panel instead of a row of empty
    # gridlined cells -- by dropping the borders BETWEEN its cells and keeping
    # only the block's outline, not by merging them: Excel refuses to sort a
    # range containing merged cells of different sizes ("all the merged cells
    # need to be the same size"), and this sheet has to stay sortable and
    # filterable. Formats travel with their rows through a sort.
    edge = _thin_border().top
    for panel_row, panel_start, panel_end in blank_side_panels:
        for panel_col in range(panel_start, panel_end + 1):
            ws.cell(panel_row, panel_col).border = Border(
                top=edge,
                bottom=edge,
                left=edge if panel_col == panel_start else Side(),
                right=edge if panel_col == panel_end else Side(),
            )
    ws.freeze_panes = f"{get_column_letter(inf_start)}{data_row}"
    if all_rows:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(inf_end)}{final_data_row}"
    ws.print_title_rows = f"1:{header_row}"
    _prepare_sheet(ws)


def build_legacy_exceptions_sheet(wb: Workbook, result: ReconciliationResult) -> None:
    """A plain, single listing of QuickBooks-side exceptions only --
    unmatched QuickBooks rows, excluded duplicate copies, and every
    QuickBooks review-hold item -- grouped by fiscal period, styled after
    the accountant's original exceptions tab. Infinium-only exceptions
    (an unmatched or duplicate Infinium row with no QuickBooks
    counterpart) carry no accrual impact and are intentionally not
    repeated here. Genuine unresolved items are Excel's standard
    "Neutral" gold; excluded duplicate copies are "Bad" red.
    """
    ws = wb.create_sheet("Exceptions")
    qb_headers = list(result.qb_raw.columns)
    trailer_headers = ["Fiscal Period", "Exception Type", "Referenced Match Ref.", "Explanation"]
    n_qb = len(qb_headers)
    n_trailer = len(trailer_headers)

    # Two independent blocks on one sheet: general QuickBooks exceptions on
    # the left, excluded QuickBooks duplicate copies on the right -- a
    # duplicate is a definite, already-decided exclusion, not an open
    # question like the rest, so it gets its own space rather than being
    # mixed into the same list.
    qb_start = 1
    qb_end = n_qb
    left_trailer_start = qb_end + 1
    left_trailer_end = left_trailer_start + n_trailer - 1
    separator_col = left_trailer_end + 1
    dup_qb_start = separator_col + 1
    dup_qb_end = dup_qb_start + n_qb - 1
    dup_trailer_start = dup_qb_end + 1
    dup_trailer_end = dup_trailer_start + n_trailer - 1

    default_year = int(result.metadata.get("fiscal_year") or result.run_timestamp.year)
    qb_period_col = result.qb_mapping.get("period")

    def row_period(record: dict) -> Any:
        qidx = record.get("QB Index")
        if qidx is not None and qb_period_col and qidx in result.qb_work.index:
            period, _ = parse_fiscal_period(result.qb_work.at[qidx, qb_period_col], default_year)
            if period is not None:
                return period
        return None

    def sort_by_period(records: list[dict]) -> list[dict]:
        return sorted(records, key=lambda record: (row_period(record) is None, row_period(record) or 0))

    qb_side_rows = [
        record for record in result.paired_rows
        if record.get("Section") not in _LEGACY_MATCHED_SECTIONS
        and record.get("QB Index") is not None
    ]
    general_rows = sort_by_period(
        [r for r in qb_side_rows if r.get("Section") not in _LEGACY_QB_DUPLICATE_SECTIONS]
    )
    duplicate_rows = sort_by_period(
        [r for r in qb_side_rows if r.get("Section") in _LEGACY_QB_DUPLICATE_SECTIONS]
    )

    fiscal_summary = build_fiscal_exception_summary(result)
    fiscal_headers = list(fiscal_summary.columns)
    fiscal_end_col = max(len(fiscal_headers), 1)
    section_end_col = dup_trailer_end

    _write_title_band(ws, 1, qb_start, section_end_col, "EXCEPTIONS | QUICKBOOKS SIDE | BY FISCAL PERIOD", NAVY)
    _write_caption_band(
        ws, 2, qb_start, section_end_col,
        f"{len(general_rows):,} QuickBooks exception(s) at left (unmatched and review-hold items, shaded "
        f"gold) and {len(duplicate_rows):,} excluded QuickBooks duplicate copy(ies) at right (shaded red). "
        f"Infinium-only exceptions carry no accrual impact and are not repeated here. Generated "
        f"{_legacy_generated_stamp(result.run_timestamp)}.",
        NAVY,
    )

    summary_header_row = 4
    summary_data_row = summary_header_row + 1
    _write_dataframe_values(ws, fiscal_summary, summary_header_row, 1)
    _format_header(ws, summary_header_row, 1, fiscal_end_col, NAVY)
    if len(fiscal_summary):
        summary_last_row = summary_data_row + len(fiscal_summary) - 1
        _format_body_block(ws, summary_data_row, summary_last_row, 1, fiscal_end_col, NAVY_LIGHT)
        _apply_number_formats(
            ws, fiscal_headers, summary_data_row, summary_last_row, 1,
            {"Net Exception Amount"}, {"Exception Count", "Exception Quantity"},
        )
    else:
        summary_last_row = summary_header_row
    summary_total_row = summary_last_row + 1
    _write_total_row(
        ws, summary_total_row, 1, fiscal_end_col,
        {
            "Exception Count": float(fiscal_summary["Exception Count"].sum()) if len(fiscal_summary) else 0,
            "Exception Quantity": float(fiscal_summary["Exception Quantity"].sum()) if len(fiscal_summary) else 0,
            "Net Exception Amount": float(fiscal_summary["Net Exception Amount"].sum()) if len(fiscal_summary) else 0,
        },
        fiscal_headers, "TOTAL EXCEPTIONS",
    )
    _set_widths(ws, 1, fiscal_end_col, summary_header_row, summary_total_row)

    header_row = summary_total_row + 3
    data_row = header_row + 1

    def write_block(
        records: list[dict], block_qb_start: int, trailer_start: int, trailer_end: int,
        status_fill: str, bold_exception_type: bool,
    ) -> int:
        block_headers = qb_headers + trailer_headers
        block = pd.DataFrame(
            [
                _legacy_row_values(record.get("QB Index"), record.get("QB Record Scope"), result.qb_work, None, qb_headers)
                + [
                    row_period(record),
                    _legacy_section_label(str(record.get("Section", ""))),
                    record.get("Referenced Match Ref.") or None,
                    f"{record.get('Match Result', '')} -- {record.get('Explanation', '')}",
                ]
                for record in records
            ],
            columns=block_headers,
        )
        _write_dataframe_values(ws, block, header_row, block_qb_start)
        _format_header(ws, header_row, block_qb_start, block_qb_start + n_qb - 1, NAVY)
        _format_header(ws, header_row, trailer_start, trailer_end, SLATE)
        block_final_row = data_row + max(len(records), 1) - 1
        for offset in range(len(records)):
            row = data_row + offset
            _apply_default_alignment(ws, row, block_qb_start, trailer_end)
            _apply_legacy_status_fill(ws, row, block_qb_start, trailer_end, status_fill)
            # The Exception Type cell (trailer's second column) is the one
            # status cell worth bolding, and only for a genuine open item.
            _apply_legacy_status_cell(ws, row, trailer_start + 1, status_fill, bold_exception_type)
            ws.cell(row, trailer_end).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            ws.cell(row, trailer_start + 2).alignment = Alignment(horizontal="center", vertical="center")
        _apply_number_formats(ws, qb_headers, data_row, block_final_row, block_qb_start,
                              {result.qb_mapping["amount"]}, {result.qb_mapping.get("quantity") or ""})
        _legacy_standardize_dates(ws, qb_headers, block_qb_start, data_row, block_final_row)
        _set_widths(ws, block_qb_start, block_qb_start + n_qb - 1, header_row, block_final_row, maximum=40)
        _standardize_legacy_widths(ws, qb_headers, block_qb_start, result.qb_mapping)
        _legacy_ensure_headers_fit(ws, qb_headers, block_qb_start)
        ws.column_dimensions[get_column_letter(trailer_start)].width = 14
        ws.column_dimensions[get_column_letter(trailer_start + 1)].width = 34
        ws.column_dimensions[get_column_letter(trailer_start + 2)].width = 16
        ws.column_dimensions[get_column_letter(trailer_end)].width = 60
        return block_final_row

    general_final_row = write_block(
        general_rows, qb_start, left_trailer_start, left_trailer_end, LEGACY_REVIEW_FILL, True,
    )
    duplicate_final_row = write_block(
        duplicate_rows, dup_qb_start, dup_trailer_start, dup_trailer_end, LEGACY_EXCLUDED_FILL, False,
    )
    final_data_row = max(general_final_row, duplicate_final_row)

    # See the equivalent fix in build_raw_data_sheet -- a ColumnDimension has
    # no renderable fill; every cell in the column must be painted.
    separator_letter = get_column_letter(separator_col)
    ws.column_dimensions[separator_letter].width = 3.5
    for row in range(1, final_data_row + 1):
        ws.cell(row, separator_col).fill = PatternFill("solid", fgColor=METHOD_GREY_DARK)
    ws.freeze_panes = f"{get_column_letter(qb_start)}{data_row}"
    if general_rows:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(left_trailer_end)}{general_final_row}"
    ws.print_title_rows = "1:2"
    _prepare_sheet(ws)


def build_legacy_workbook(result: ReconciliationResult) -> bytes:
    """The 'Accountant's Legacy Download' -- a simplified export mirroring
    the reviewer's original hand-built workbook (QuickBooks left, Infinium
    right, exceptions on their own tab by fiscal period) with Excel's
    standard Good/Neutral/Bad coloring, meant to be read on sight without
    the audit-trail depth of the primary workpaper. The primary workpaper
    and analytics package are unaffected by this export.
    """
    validate_match_references(result)
    wb = Workbook()
    wb.properties.creator = "Sales Reconciliation Application"
    wb.properties.title = f"Sales Reconciliation (Legacy Format) {result.run_id}"
    wb.properties.subject = "QuickBooks to Infinium reconciliation, accountant's legacy layout"
    wb.properties.description = (
        "Simplified accountant's legacy-format export generated from one controlled reconciliation run."
    )
    build_legacy_reconciliation_sheet(wb, result)
    build_legacy_exceptions_sheet(wb, result)
    build_aggregates_sheet(wb, result)
    _apply_workbook_run_metadata(wb, result)
    # Legacy Reconciliation and Exceptions set their own deliberate,
    # type-appropriate column widths (see _standardize_legacy_widths) --
    # skip the workbook-wide content-driven autofit pass for them so those
    # widths actually stick instead of being overwritten by it.
    return _save_workbook_bytes(
        wb, apply_accountant_row_heights=True,
        skip_autofit_titles=frozenset({"Legacy Reconciliation", "Exceptions"}),
        suppress_text_number_warnings=True,
    )
