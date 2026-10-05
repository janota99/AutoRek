"""``build_primary_workbook``: assembles the primary accounting workpaper."""

from __future__ import annotations

import dataclasses

from openpyxl import Workbook

from ..matching import assert_exportable, ReconciliationResult, validate_match_references
from .audit import build_audit_sheet
from .finishing import _apply_workbook_run_metadata, _save_workbook_bytes
from .raw_data import build_raw_data_sheet
from .detail import (
    _link_reconciled_data_to_unresolved_exceptions,
    build_reconciliation_detail_sheet,
)
from .unresolved import build_unresolved_sheet
from .summary_sheets import build_aggregates_sheet, build_posting_summary_sheet


def build_primary_workbook(result: ReconciliationResult) -> bytes:
    validate_match_references(result)
    # Re-run the identity controls on exactly what is about to be written --
    # including any reviewer adjustments -- and refuse to export on a failure.
    result = dataclasses.replace(result, export_controls=assert_exportable(result))
    wb = Workbook()
    wb.properties.creator = "Sales Reconciliation Application"
    wb.properties.title = f"Sales Reconciliation {result.run_id}"
    wb.properties.subject = "QuickBooks to Infinium reconciliation and journal-entry support"
    wb.properties.description = "Accounting workpaper generated from one controlled reconciliation run."
    # Creation order doubles as the final tab order (aside from Posting
    # Summary, moved to the front below): Reconciliation Detail, Unresolved
    # Exceptions, Aggregates, Raw Data.
    build_reconciliation_detail_sheet(wb, result)
    build_unresolved_sheet(wb, result)
    _link_reconciled_data_to_unresolved_exceptions(wb, result)
    build_aggregates_sheet(wb, result, sheet_title="Aggregates")
    build_raw_data_sheet(wb, result)
    build_audit_sheet(wb, result)
    build_posting_summary_sheet(wb, result)
    # The landing page: moved to the very front now that every other sheet exists.
    wb.move_sheet("Posting Summary", offset=-wb.sheetnames.index("Posting Summary"))
    # Workbook() always starts with one default "Sheet"; every real sheet
    # above was added via create_sheet, so the stray default is still here
    # and empty (build_data_search_sheet used to claim it by renaming it --
    # now that it's gone, nothing does).
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]
    wb.active = 0
    _apply_workbook_run_metadata(wb, result)
    return _save_workbook_bytes(wb, apply_accountant_row_heights=True, suppress_text_number_warnings=True)
