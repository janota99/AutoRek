"""The Raw Data sheet: every source row with its reconciliation outcome."""

from __future__ import annotations

from typing import Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter

from ..config import METHOD_GREY_DARK, NAVY, NAVY_LIGHT, TEAL, TEAL_LIGHT
from ..excel_styles import (
    _apply_duplicate_style,
    _apply_number_formats,
    _format_body_block,
    _format_header,
    _prepare_sheet,
    _set_widths,
    _write_caption_band,
    _write_title_band,
    _write_total_row,
)
from ..matching import ReconciliationResult
from .tables import (
    _add_plain_data_table,
    _duplicate_source_indexes,
    _fiscal_period_prefix,
    _source_totals,
    _write_dataframe_values,
)


def _resolve_paired_records_bulk(result: ReconciliationResult) -> list[dict[str, Any]]:
    """Bulk upgrade paired-row records using native dicts to prevent O(N) DataFrame lookups."""
    resolved_list = [dict(record) for record in result.paired_rows]

    # Current matching results already contain explicit source scopes. Return
    # them immediately and avoid rebuilding information that is already known.
    if all(
        "QB Record Scope" in record and "Infinium Record Scope" in record
        for record in resolved_list
    ):
        return resolved_list

    # Legacy results may require their scopes and indexes to be reconstructed
    # from historical-clearance evidence. A grouped clearance intentionally
    # repeats its Clearance ID across multiple sequences, so Clearance ID alone
    # is not unique; the sequence is part of the lookup key.
    clearances = getattr(result, "historical_clearances", pd.DataFrame())
    clearance_map: dict[tuple[Any, Any], dict[str, Any]] = {}
    clearance_fallback: dict[Any, dict[str, Any]] = {}
    if not clearances.empty and "Clearance ID" in clearances.columns:
        for clearance in clearances.to_dict("records"):
            clearance_id = clearance.get("Clearance ID")
            sequence = clearance.get("Group Sequence", 1)
            clearance_map[(clearance_id, sequence)] = clearance
            clearance_fallback.setdefault(clearance_id, clearance)

    for resolved in resolved_list:
        if "QB Record Scope" in resolved and "Infinium Record Scope" in resolved:
            continue

        resolved["QB Record Scope"] = "Primary" if resolved.get("QB Index") is not None else None
        resolved["Infinium Record Scope"] = (
            "Primary" if resolved.get("Infinium Index") is not None else None
        )
        if resolved.get("Section") == "01 Matched - Historical Clearance":
            clearance_id = resolved.get("Match ID")
            sequence = resolved.get("Group Sequence", 1)
            clearance = clearance_map.get(
                (clearance_id, sequence), clearance_fallback.get(clearance_id)
            )
            if clearance:
                primary_is_qb = clearance["Primary Dataset"] == "QuickBooks Primary"
                qb_index = (
                    clearance["Primary Row Index"]
                    if primary_is_qb else clearance["Secondary Row Index"]
                )
                inf_index = (
                    clearance["Secondary Row Index"]
                    if primary_is_qb else clearance["Primary Row Index"]
                )
                resolved["QB Index"] = (
                    None if qb_index is None or pd.isna(qb_index) else int(qb_index)
                )
                resolved["Infinium Index"] = (
                    None if inf_index is None or pd.isna(inf_index) else int(inf_index)
                )
                resolved["QB Record Scope"] = (
                    ("Primary" if primary_is_qb else "Historical")
                    if resolved["QB Index"] is not None else None
                )
                resolved["Infinium Record Scope"] = (
                    ("Historical" if primary_is_qb else "Primary")
                    if resolved["Infinium Index"] is not None else None
                )

    return resolved_list


def build_raw_data_sheet(wb: Workbook, result: ReconciliationResult) -> None:
    ws = wb.create_sheet("Raw Data")
    qb_headers = list(result.qb_raw.columns)
    inf_headers = list(result.inf_raw.columns)
    qb_start = 1
    separator_col = len(qb_headers) + 1
    inf_start = separator_col + 1
    header_row = 3
    data_row = 4
    qb_end = len(qb_headers)
    inf_end = inf_start + len(inf_headers) - 1

    _write_title_band(
        ws, 1, qb_start, qb_end, f"{_fiscal_period_prefix(result)} | QUICKBOOKS | RAW TRANSACTION DETAIL", NAVY,
    )
    _write_title_band(ws, 1, inf_start, inf_end, "INFINIUM | RAW UPLOAD", TEAL)
    _write_caption_band(
        ws, 2, qb_start, qb_end,
        f"{len(result.qb_raw):,} rows | Source control total: ${result.metrics['QuickBooks Source Total']:,.2f} | "
        f"{result.metrics['QuickBooks Subtotal Rows Excluded']:,} subtotal row(s) excluded before matching.",
        NAVY,
    )
    _write_caption_band(
        ws, 2, inf_start, inf_end,
        f"{len(result.inf_raw):,} rows | Source control total: ${result.metrics['Infinium Source Total']:,.2f} | "
        "Values preserved before matching.",
        TEAL,
    )
    _write_dataframe_values(ws, result.qb_raw, header_row, qb_start)
    _write_dataframe_values(ws, result.inf_raw, header_row, inf_start)
    qb_amount_cols = {result.qb_mapping["amount"]}
    qb_quantity_cols = {result.qb_mapping.get("quantity") or ""}
    inf_amount_cols = {result.inf_mapping["amount"]}
    _format_header(ws, header_row, qb_start, qb_end, NAVY, qb_headers, qb_amount_cols, qb_quantity_cols)
    _format_header(ws, header_row, inf_start, inf_end, TEAL, inf_headers, inf_amount_cols)
    _format_body_block(ws, data_row, data_row + len(result.qb_raw) - 1, qb_start, qb_end, NAVY_LIGHT)
    _format_body_block(ws, data_row, data_row + len(result.inf_raw) - 1, inf_start, inf_end, TEAL_LIGHT)
    for source_index in _duplicate_source_indexes(result, "QuickBooks"):
        if 0 <= source_index < len(result.qb_raw):
            _apply_duplicate_style(ws, data_row + source_index, qb_start, qb_end)
    for source_index in _duplicate_source_indexes(result, "Infinium"):
        if 0 <= source_index < len(result.inf_raw):
            _apply_duplicate_style(ws, data_row + source_index, inf_start, inf_end)
    qb_total_row = data_row + len(result.qb_raw)
    inf_total_row = data_row + len(result.inf_raw)
    _write_total_row(ws, qb_total_row, qb_start, qb_end, _source_totals(result.qb_raw, result.qb_mapping), qb_headers, "SOURCE TOTAL")
    _write_total_row(ws, inf_total_row, inf_start, inf_end, _source_totals(result.inf_raw, result.inf_mapping), inf_headers, "SOURCE TOTAL")
    _apply_number_formats(ws, qb_headers, data_row, qb_total_row, qb_start,
                          qb_amount_cols, qb_quantity_cols)
    _apply_number_formats(ws, inf_headers, data_row, inf_total_row, inf_start,
                          inf_amount_cols, set())
    # A ColumnDimension has no renderable fill of its own -- painting every
    # cell in the column is what actually gives the two source tables a
    # visible dividing band, the same technique Reconciliation Detail's grey
    # "Match Result" column already uses to separate its two ledgers.
    separator_letter = get_column_letter(separator_col)
    ws.column_dimensions[separator_letter].width = 3.5
    for row in range(1, max(qb_total_row, inf_total_row) + 1):
        ws.cell(row, separator_col).fill = PatternFill("solid", fgColor=METHOD_GREY_DARK)
    _add_plain_data_table(
        ws, table_name="RawDataQuickBooks", start_col=qb_start, end_col=qb_end,
        header_row=header_row, last_data_row=qb_total_row - 1,
    )
    _add_plain_data_table(
        ws, table_name="RawDataInfinium", start_col=inf_start, end_col=inf_end,
        header_row=header_row, last_data_row=inf_total_row - 1,
    )
    _set_widths(ws, qb_start, qb_end, header_row, qb_total_row)
    _set_widths(ws, inf_start, inf_end, header_row, inf_total_row)
    ws.freeze_panes = f"{get_column_letter(inf_start)}{data_row}"
    ws.print_title_rows = "1:3"
    _prepare_sheet(ws)
