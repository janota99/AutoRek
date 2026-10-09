"""The Unresolved Exceptions sheet."""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, PatternFill, Protection
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from ..config import (
    ACCOUNTING_COUNT_FORMAT,
    ACCOUNTING_CURRENCY_FORMAT,
    AMBER,
    GREEN_LIGHT,
    NAVY,
    NAVY_LIGHT,
    RED_LIGHT,
    SLATE,
    SLATE_LIGHT,
)
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
    fix_row_height,
)
from ..matching.review_decisions import WORKBOOK_DISPOSITION
from ..matching import (
    AMOUNT_CENTS,
    build_fiscal_exception_summary,
    cents_or_zero,
    cents_to_float,
    flag_mask,
    po_reuse_error_qb_index_map,
    PRIOR_PERIOD_URGENT_THRESHOLD,
    QB_ID,
    ReconciliationResult,
    short_reason_code,
)
from .tables import (
    _add_exception_table,
    _duplicate_source_indexes,
    _fiscal_period_prefix,
    _je_support_manual_exclusions_expr,
    _review_holds_released_expr,
    _table_column_reference,
    _write_dataframe_values,
    UNRESOLVED_EXCEPTIONS_SHEET,
)
from .detail import (
    _apply_row_id_hyperlink,
    _detail_match_ref_letter,
    _qb_id_detail_row_map,
    _style_match_ref_link,
)
from .sheet_parts import (
    _simplify_duplicate_display,
    _simplify_po_reuse_display,
    _write_color_legend,
    _write_kpi_band,
    _write_reason_code_legend,
)


# Appended to the row-1 title: row 1 is the only summary row still showing
# when the group is collapsed, so it has to say what is folded away.

SUMMARY_GROUP_MARKER = (
    "⊞ GROUPED SUMMARY — click + / – beside row 1 to show or hide KPIs, reason codes, "
    "and exceptions by fiscal period"
)


def _group_summary_rows(ws, first_row: int, last_row: int) -> None:
    """Fold the summary block (caption, KPIs, legends, reason codes, the
    fiscal-period table) into one collapsed outline group, so the sheet opens
    on the exceptions table. The +/- button sits beside row 1, above the
    group, and the frozen area shrinks to the title and header rows.

    Only outlineLevel/hidden are set here; the row-height passes in
    finishing.py set .height alone, so they leave the group intact."""
    for row in range(first_row, last_row + 1):
        dimension = ws.row_dimensions[row]
        dimension.outlineLevel = 1
        dimension.hidden = True
    ws.row_dimensions[first_row - 1].collapsed = True
    ws.sheet_properties.outlinePr.summaryBelow = False
    ws.sheet_format.outlineLevelRow = 1


def _decision_cells_by_row(result: ReconciliationResult):
    """QuickBooks row ID -> (Reviewer Disposition, Review Date, Comment)
    for every decision applied in the application, so the workbook opens with
    the same decisions the Audit & Controls sheet lists. A row without one stays
    Pending Review. The engine's own columns are never touched."""
    cells = {}
    adjustments = result.review_adjustments
    if adjustments is None or adjustments.empty:
        return lambda row_id: ("Pending Review", "", "")
    for record in adjustments.to_dict("records"):
        comment = record["Reason"]
        if record["Support Reference"]:
            comment += f" | Support: {record['Support Reference']}"
        if record["Infinium Row IDs"]:
            comment += f" | Matched to: {record['Infinium Row IDs']}"
        cells[record["QuickBooks Row ID"]] = (
            WORKBOOK_DISPOSITION[record["Reviewer Action"]], record["Decision Date"], comment,
        )
    return lambda row_id: cells.get(row_id, ("Pending Review", "", ""))


def build_unresolved_sheet(wb: Workbook, result: ReconciliationResult) -> None:
    ws = wb.create_sheet(UNRESOLVED_EXCEPTIONS_SHEET)
    decision_cells = _decision_cells_by_row(result)

    # QuickBooks is the sole accrual and journal-entry basis, so this sheet
    # keeps everything with accrual relevance -- the raw QuickBooks exception
    # population on the left, and the QuickBooks items excluded from that
    # same population as duplicates on the right. Infinium exceptions carry
    # no accrual impact and are already listed in Reconciliation Detail, so they
    # are intentionally not repeated here.
    source_headers = list(result.qb_raw.columns)
    headers = source_headers + [
        "Referenced Match Ref.", "Exception Status", "Reference Amount Difference",
        "Reviewer Disposition", "Review Date", "Comment",
    ]
    referenced_offset = len(source_headers) + 1
    status_offset = referenced_offset + 1
    difference_offset = status_offset + 1
    # This row's Final Disposition is always TRUE_UNMATCHED (the engine's own,
    # immutable conclusion -- see the QB Disposition Ledger). Reviewer
    # Disposition is a SEPARATE manual layer: normally the row simply stays in
    # the automated JE, and only "Exclude" (with a documented reason) pulls
    # it out -- see the Posting Summary bridge (Manual JE Exclusions).
    disposition_offset = difference_offset + 1
    review_date_offset = disposition_offset + 1
    note_offset = review_date_offset + 1
    # An exception can point at an accepted match two ways -- a consumed
    # PO/invoice candidate, or a duplicate group with a matched member --
    # both already resolved onto its paired row.
    referenced_by_qb_index = {
        int(row["QB Index"]): row.get("Referenced Match Ref.", "")
        for row in result.paired_rows
        if row.get("Section") == "02 Unmatched QuickBooks" and row.get("QB Index") is not None
    }
    candidate_map = result.candidates.set_index("QuickBooks Row ID").to_dict("index") if not result.candidates.empty else {}
    qb_id_row_map = _qb_id_detail_row_map(result)
    # PO Re-use Error rows are never withheld from the accrual (unlike
    # every other review-hold classification on this sheet), so they still
    # come through result.unmatched_qb in the loop below -- this lookup
    # just overrides their Exception Status label to make the reused-PO
    # grouping traceable instead of showing a generic "no match" reason.
    po_reuse_qb_map = po_reuse_error_qb_index_map(result.po_reuse_errors)
    po_reuse_detail = (
        result.po_reuse_errors.set_index("PO Reuse ID").to_dict("index")
        if not result.po_reuse_errors.empty else {}
    )

    qb_subset_dict = result.qb_work.loc[result.unmatched_qb].to_dict("index")
    records = []
    for qidx in result.unmatched_qb:
        row_data = qb_subset_dict[qidx]
        candidate = candidate_map.get(row_data[QB_ID], {})
        po_reuse_id = po_reuse_qb_map.get(qidx)
        if po_reuse_id:
            exception_status = "PO Re-use Error"
            reference_amount_difference = po_reuse_detail.get(po_reuse_id, {}).get("Difference")
        else:
            exception_status = candidate.get("Disposition", "Unmatched QuickBooks")
            reference_amount_difference = candidate.get("Minimum Amount Difference")
        records.append(
            [row_data.get(col) for col in source_headers]
            + [
                referenced_by_qb_index.get(int(qidx)) or None,
                exception_status,
                reference_amount_difference,
                *decision_cells(row_data[QB_ID]),
            ]
        )
    frame = pd.DataFrame(records, columns=headers)
    end_col = len(headers)
    amount_col_position = source_headers.index(result.qb_mapping["amount"]) + 1

    # duplicate_analysis carries every disposition tier (canonical, excess,
    # resolved-via-match, and held-for-review) in one report for full audit
    # traceability. Each tier means something different for the JE, so the
    # Excel output splits them: canonical/excess get their own section
    # below, review-hold gets a distinct section further down, and
    # resolved-via-match candidates need no special display at all since
    # they proceeded normally with no exclusion.
    duplicate_frame = result.duplicate_analysis.loc[
        result.duplicate_analysis["Disposition"].isin(
            ["Retained canonical row", "Excluded excess copy"]
        )
    ].copy()
    duplicate_frame["Reviewer Note"] = ""
    # Capture styling inputs from the full technical frame before reducing it
    # to the reviewer-facing view below -- only the excess copy is actually
    # excluded from the JE, and that flag isn't part of the simplified columns.
    duplicate_excluded_flags = flag_mask(duplicate_frame["Automatically Excluded"]).tolist()
    duplicate_frame = _simplify_duplicate_display(duplicate_frame)
    duplicate_headers = list(duplicate_frame.columns)
    dup_end_col = len(duplicate_headers)
    duplicate_excluded_count = result.metrics["Duplicate QuickBooks Rows"]
    duplicate_amount_total = result.metrics["Duplicate QuickBooks Amount"]

    # Every QuickBooks row on REVIEW_HOLD -- the same population the
    # disposition ledger, Posting Summary, and Legacy sheet all show -- in ONE
    # table, replacing what used to be four separately-labeled sections
    # (Duplicate Review Hold, Amount Variance, Ambiguous, Reference Evidence).
    # A short Reason Code plus a one-line Reason keep the sheet scannable; the
    # full technical explanation for each code actually used this run is in
    # this sheet's own embedded glossary, in the frozen rows above.
    review_ledger = result.qb_dispositions.loc[
        result.qb_dispositions["Final Disposition"] == "REVIEW_HOLD"
    ].copy()
    qb_by_id = result.qb_work.set_index(QB_ID)
    po_field, invoice_field = result.qb_mapping["po"], result.qb_mapping["invoice"]

    def _field(row_id: str, column: Optional[str]) -> Any:
        if not column or row_id not in qb_by_id.index:
            return None
        return qb_by_id.at[row_id, column]

    # Reference-evidence holds and reference-matched amount variances each
    # carry the specific Infinium amount and difference; folded in here so a
    # reviewer sees both sides of an amount conflict without leaving this table.
    reference_amount_frames = [
        result.reference_hold_analysis[["QuickBooks Row ID", "Infinium Amount", "Amount Difference"]]
        if not result.reference_hold_analysis.empty else None,
        result.amount_variance_analysis.rename(
            columns={"Potential Difference": "Amount Difference"},
        )[["QuickBooks Row ID", "Infinium Amount", "Amount Difference"]]
        if not result.amount_variance_analysis.empty else None,
    ]
    reference_amount_frames = [frame for frame in reference_amount_frames if frame is not None]
    reference_amounts = (
        pd.concat([f.astype(object) for f in reference_amount_frames], ignore_index=True).set_index("QuickBooks Row ID")
        if reference_amount_frames else pd.DataFrame(columns=["Infinium Amount", "Amount Difference"])
    )
    review_frame = pd.DataFrame({
        "Review ID": review_ledger["Review ID"].values,
        "Row ID": review_ledger["QBO Row ID"].values,
        "PO": [_field(row_id, po_field) for row_id in review_ledger["QBO Row ID"]],
        "Invoice": [_field(row_id, invoice_field) for row_id in review_ledger["QBO Row ID"]],
        "Amount": review_ledger["Amount"].values,
        "Infinium Amount": [
            reference_amounts.at[row_id, "Infinium Amount"] if row_id in reference_amounts.index else None
            for row_id in review_ledger["QBO Row ID"]
        ],
        "Difference": [
            reference_amounts.at[row_id, "Amount Difference"] if row_id in reference_amounts.index else None
            for row_id in review_ledger["QBO Row ID"]
        ],
        "Reason Code": [short_reason_code(code) for code in review_ledger["Reason Code"]],
        "Reason": review_ledger["Final Reason"].values,
        "Referenced Match Ref.": review_ledger["Related Match Ref."].values,
        "Related Infinium Row IDs": review_ledger["Related Infinium Row IDs"].values,
    })
    review_frame["Candidate Evidence"] = review_ledger["Candidate Evidence"].values
    decided = [decision_cells(row_id) for row_id in review_ledger["QBO Row ID"]]
    review_frame["Reviewer Disposition"] = [d[0] for d in decided]
    review_frame["Review Date"] = [d[1] for d in decided]
    review_frame["Comment"] = [d[2] for d in decided]
    review_headers = list(review_frame.columns)
    review_end_col = len(review_headers)
    review_hold_count = result.metrics["Final Disposition - Review Hold Rows"]
    review_hold_amount = result.metrics["Final Disposition - Review Hold Amount"]

    # PO Re-use Error (see build_po_reuse_errors in matching/exceptions.py): a
    # normalized PO reused across 2+ still-unresolved QuickBooks rows whose
    # grouped total does not tie exactly to the grouped Infinium total for
    # that PO. Unlike every section above, these rows are NOT withheld --
    # they already appear in the exceptions table above, counted in the
    # accrual total. This is purely the grouped detail view.
    po_reuse_frame = _simplify_po_reuse_display(result.po_reuse_errors)
    po_reuse_headers = list(po_reuse_frame.columns)
    po_reuse_end_col = len(po_reuse_headers)
    po_reuse_group_count = result.metrics["PO Re-use Error Groups"]
    po_reuse_qb_row_count = result.metrics["PO Re-use Error QuickBooks Rows"]
    po_reuse_net_difference = result.metrics["PO Re-use Error Net Difference"]

    unmatched_qb_amounts = result.qb_work.loc[result.unmatched_qb, AMOUNT_CENTS].tolist()
    amounts = [cents_or_zero(val) for val in unmatched_qb_amounts]
    net = sum(amounts)

    qb_table_name = "QuickBooksExceptions"
    qb_amount_column_expr = _table_column_reference(qb_table_name, result.qb_mapping["amount"])
    qb_amount_sum_expr = f"SUM({qb_amount_column_expr})"

    _write_title_band(
        ws, 1, 1, end_col,
        f"{_fiscal_period_prefix(result)} | QUICKBOOKS EXCEPTIONS | JOURNAL ENTRY SUPPORT"
        f"    {SUMMARY_GROUP_MARKER}",
        NAVY,
    )
    _write_caption_band(
        ws, 2, 1, end_col,
        f"Only TRUE UNMATCHED QuickBooks transactions -- no Infinium support after every matching pass -- feed the "
        f"proposed journal entry. Duplicates and review holds are excluded and itemized below. "
        "Review every exception before posting.",
        NAVY,
    )
    duplicate_caption = (
        f"{len(duplicate_frame):,} QuickBooks item(s) belong to a strong duplicate group (identical "
        "normalized PO, invoice, and signed amount). One canonical row per group is retained and remains "
        f"active; {duplicate_excluded_count:,} excess "
        f"{'copy is' if duplicate_excluded_count == 1 else 'copies are'} excluded from the accrual/JE "
        "support total in the exceptions table above. If a pair turns out to be a legitimate repeated "
        "transaction rather than a duplicate entry, the excess copy must be added to the JE support "
        "manually."
        if len(duplicate_frame)
        else "No QuickBooks exact duplicates (matching PO, invoice, and amount) were identified."
    )

    kpis = [
        ("Unresolved Rows", f"=IFERROR(ROWS({qb_amount_column_expr}),0)", ACCOUNTING_COUNT_FORMAT),
        (
            "Proposed Debit Support",
            f'=SUMIF({qb_amount_column_expr},">0",{qb_amount_column_expr})',
            ACCOUNTING_CURRENCY_FORMAT,
        ),
        (
            "Proposed Credit Support",
            f'=ABS(SUMIF({qb_amount_column_expr},"<0",{qb_amount_column_expr}))',
            ACCOUNTING_CURRENCY_FORMAT,
        ),
        (
            "Engine Proposed JE Support",
            f"={qb_amount_sum_expr}",
            ACCOUNTING_CURRENCY_FORMAT,
        ),
    ]
    _write_kpi_band(ws, 3, 4, kpis, end_col)
    legend_last_row = _write_color_legend(ws, 5, 1, end_col)
    # Reason Code Glossary embedded here (frozen, above the data), scoped to
    # only the codes this run's Review Hold table actually uses -- replaces
    # the old dedicated glossary sheet with no irrelevant codes listed.
    run_reason_codes = sorted(set(str(code) for code in review_frame["Reason Code"] if code))
    legend_last_row = _write_reason_code_legend(ws, legend_last_row + 1, 1, end_col, run_reason_codes)
    # One slim spacer row, then the fiscal-period summary.
    fix_row_height(ws, legend_last_row + 1, 8)

    # QuickBooks exceptions by fiscal period -- promoted above the detail
    # tables so period-level review (count and net amount per period) never
    # requires scrolling past the full exception and duplicate lists.
    fiscal_summary = build_fiscal_exception_summary(result)
    fiscal_headers = list(fiscal_summary.columns)
    fiscal_title_row = legend_last_row + 2
    fiscal_caption_row = fiscal_title_row + 1
    fiscal_header_row = fiscal_title_row + 2
    fiscal_data_row = fiscal_header_row + 1
    fiscal_end_col = len(fiscal_headers)
    fiscal_section_end_col = end_col
    selected_period = result.metadata.get("fiscal_period")
    has_fiscal_period = bool(result.qb_mapping.get("period"))
    _write_title_band(
        ws, fiscal_title_row, 1, fiscal_section_end_col,
        (
            "QUICKBOOKS EXCEPTIONS BY FISCAL PERIOD | CURRENT VS PRIOR PERIODS"
            if has_fiscal_period
            else "QUICKBOOKS EXCEPTIONS BY FISCAL PERIOD | FISCAL PERIOD NOT AVAILABLE"
        ),
        NAVY,
    )
    quantity_note = (
        "Exception quantity is sourced from the mapped QuickBooks quantity column."
        if result.qb_mapping.get("quantity")
        else "No QuickBooks quantity column was mapped; exception quantities are shown as zero."
    )
    _write_caption_band(
        ws, fiscal_caption_row, 1, fiscal_section_end_col,
        (
            f"Selected current reporting period: PD-{int(selected_period):02d}. A QuickBooks fiscal period up to "
            f"{PRIOR_PERIOD_URGENT_THRESHOLD} period(s) behind is a Prior Period exception; anything "
            f"older is an Urgent Prior Period exception. {quantity_note}"
            if has_fiscal_period and selected_period is not None
            else f"No current reporting period was selected. Exceptions are summarized by source period without current/prior classification. {quantity_note}"
            if has_fiscal_period
            else "No credible QuickBooks fiscal-period identifier was found or mapped. Period-based "
            f"classification is disabled and all exceptions are summarized together. {quantity_note}"
        ),
        NAVY,
    )
    _write_dataframe_values(ws, fiscal_summary, fiscal_header_row, 1)
    _format_header(
        ws, fiscal_header_row, 1, fiscal_end_col, NAVY,
        headers=fiscal_headers, amount_columns={"Net Exception Amount"},
        quantity_columns={"Exception Count", "Exception Quantity"},
    )
    if len(fiscal_summary):
        fiscal_last_row = fiscal_data_row + len(fiscal_summary) - 1
        _format_body_block(ws, fiscal_data_row, fiscal_last_row, 1, fiscal_end_col, NAVY_LIGHT)
        # Only the classification cell carries the color: tinting the whole
        # row makes the summary compete with the exception table below it.
        classification_col = fiscal_headers.index("Period Classification") + 1
        for offset, classification in enumerate(fiscal_summary["Period Classification"], start=fiscal_data_row):
            fill = (
                RED_LIGHT
                if classification == "Urgent Prior Period"
                else GREEN_LIGHT
                if classification == "Current Period"
                else AMBER
            )
            ws.cell(offset, classification_col).fill = PatternFill("solid", fgColor=fill)
        _apply_number_formats(
            ws, fiscal_headers, fiscal_data_row, fiscal_last_row, 1,
            {"Net Exception Amount"}, {"Exception Count", "Exception Quantity"},
        )
    else:
        fiscal_last_row = fiscal_header_row
    fiscal_total_row = fiscal_last_row + 1
    _write_total_row(
        ws, fiscal_total_row, 1, fiscal_end_col,
        {
            "Exception Count": float(fiscal_summary["Exception Count"].sum()) if len(fiscal_summary) else 0,
            "Exception Quantity": float(fiscal_summary["Exception Quantity"].sum()) if len(fiscal_summary) else 0,
            "Net Exception Amount": float(fiscal_summary["Net Exception Amount"].sum()) if len(fiscal_summary) else 0,
        },
        fiscal_headers,
        "TOTAL EXCEPTIONS",
    )
    _set_widths(ws, 1, fiscal_end_col, fiscal_header_row, fiscal_total_row)
    ws.column_dimensions["B"].width = max(ws.column_dimensions["B"].width or 0, 34)

    header_row = fiscal_total_row + 3
    data_row = header_row + 1
    qb_quantity_header = result.qb_mapping.get("quantity")
    _write_dataframe_values(ws, frame, header_row, 1)
    _format_header(
        ws, header_row, 1, end_col, NAVY,
        headers=headers, amount_columns={result.qb_mapping["amount"]},
        quantity_columns={qb_quantity_header or ""},
    )
    # Centered, like the references beneath it, so it never reads as one run
    # of text with the right-aligned amount heading beside it.
    ws.cell(header_row, referenced_offset).alignment = Alignment(
        horizontal="center", vertical="center", wrap_text=True,
    )
    detail_ref_letter = _detail_match_ref_letter(wb)
    if len(frame):
        _format_body_block(ws, data_row, data_row + len(frame) - 1, 1, end_col, NAVY_LIGHT)
        duplicate_qb_rows = _duplicate_source_indexes(result, "QuickBooks")
        for offset, qidx in enumerate(result.unmatched_qb):
            row = data_row + offset
            ws.cell(row, source_headers.index(result.qb_mapping["amount"]) + 1).number_format = ACCOUNTING_CURRENCY_FORMAT
            ws.cell(row, status_offset).fill = PatternFill("solid", fgColor=AMBER)
            ws.cell(row, status_offset).alignment = Alignment(wrap_text=True, vertical="center")
            ws.cell(row, referenced_offset).alignment = Alignment(horizontal="center", vertical="center")
            pointer = referenced_by_qb_index.get(int(qidx))
            if pointer and detail_ref_letter:
                # Jump straight to the accepted match this exception points at.
                _style_match_ref_link(ws.cell(row, referenced_offset), pointer, detail_ref_letter)
            ws.cell(row, difference_offset).number_format = ACCOUNTING_CURRENCY_FORMAT
            if int(qidx) in duplicate_qb_rows:
                _apply_duplicate_style(ws, row, 1, end_col)
    total_row = data_row + len(frame)
    _write_total_row(
        ws, total_row, 1, end_col,
        {result.qb_mapping["amount"]: cents_to_float(net)}, headers,
        "PROPOSED JE SUPPORT TOTAL",
    )
    ws.cell(total_row, amount_col_position).number_format = ACCOUNTING_CURRENCY_FORMAT

    qb_summed_headers = {result.qb_mapping["amount"]}
    if qb_quantity_header and qb_quantity_header in headers:
        qb_summed_headers.add(qb_quantity_header)
    _add_exception_table(
        ws,
        table_name=qb_table_name,
        headers=headers,
        header_row=header_row,
        total_row=total_row,
        start_col=1,
        total_label="PROPOSED JE SUPPORT TOTAL",
        summed_headers=qb_summed_headers,
        style_name="TableStyleMedium2",
    )

    _apply_number_formats(
        ws, headers, data_row, total_row, 1,
        {result.qb_mapping["amount"]}, {qb_quantity_header or ""},
    )
    ws.column_dimensions[get_column_letter(status_offset)].width = 48
    ws.column_dimensions[get_column_letter(difference_offset)].width = 24
    ws.column_dimensions[get_column_letter(disposition_offset)].width = 22
    ws.column_dimensions[get_column_letter(review_date_offset)].width = 14
    ws.column_dimensions[get_column_letter(note_offset)].width = 36
    _set_widths(ws, 1, len(source_headers), header_row, total_row)
    ws.freeze_panes = f"A{data_row}"
    # Everything between the title and the exceptions table. Computed per run:
    # the reason-code glossary and the fiscal-period table change length.
    _group_summary_rows(ws, 2, header_row - 1)
    if len(frame):
        note_col = get_column_letter(note_offset)
        validation = DataValidation(
            type="textLength", operator="lessThanOrEqual", formula1="1000", allow_blank=True
        )
        validation.error = "Comments are limited to 1,000 characters."
        validation.errorTitle = "Comment too long"
        ws.add_data_validation(validation)
        validation.add(f"{note_col}{data_row}:{note_col}{header_row + len(frame)}")

        je_disposition_validation = DataValidation(
            type="list", formula1='"Pending Review,Exclude"', allow_blank=False,
        )
        je_disposition_validation.error = (
            "Select Pending Review (the automated conclusion stands) or Exclude (documented reason "
            "required) -- the engine's own Final Disposition is never changed by this selection."
        )
        je_disposition_validation.errorTitle = "Reviewer disposition required"
        ws.add_data_validation(je_disposition_validation)
        disposition_col_letter = get_column_letter(disposition_offset)
        je_disposition_validation.add(
            f"{disposition_col_letter}{data_row}:{disposition_col_letter}{header_row + len(frame)}"
        )
        # Excluding a row from the automatic JE is itself an accounting decision,
        # so it must carry a documented reason -- flagged, not silently accepted,
        # if the reviewer picks Exclude and leaves the comment blank.
        je_exclude_rule = FormulaRule(
            formula=[f'AND(${disposition_col_letter}{data_row}="Exclude",${note_col}{data_row}="")'],
            fill=PatternFill("solid", fgColor=RED_LIGHT),
        )
        ws.conditional_formatting.add(f"{note_col}{data_row}:{note_col}{header_row + len(frame)}", je_exclude_rule)

    # QuickBooks duplicates excluded from the JE above, and the proposed JE
    # itself, are placed a fixed 10 rows below the exceptions total row --
    # far enough to read as clearly separate from the exception detail,
    # close enough to stay on the same review pass.
    duplicate_kpis = [
        ("Duplicate QuickBooks items excluded", duplicate_excluded_count, ACCOUNTING_COUNT_FORMAT),
        ("Amount excluded from JE", duplicate_amount_total, ACCOUNTING_CURRENCY_FORMAT),
        ("JE inclusion", "Excluded", 'General'),
    ]
    dup_title_row = total_row + 10
    dup_caption_row = dup_title_row + 1
    dup_kpi_label_row = dup_title_row + 2
    dup_kpi_value_row = dup_title_row + 3
    dup_header_row = dup_title_row + 5
    dup_data_row = dup_header_row + 1
    section_end_col = max(end_col, dup_end_col, review_end_col, po_reuse_end_col)

    _write_title_band(
        ws, dup_title_row, 1, section_end_col,
        "DUPLICATE EXCLUDED | CONFIRMED COPY - EXCLUDED FROM PROPOSED JE", NAVY,
    )
    _write_caption_band(ws, dup_caption_row, 1, section_end_col, duplicate_caption, NAVY)
    _write_kpi_band(ws, dup_kpi_label_row, dup_kpi_value_row, duplicate_kpis, dup_end_col)

    _write_dataframe_values(ws, duplicate_frame, dup_header_row, 1)
    _format_header(
        ws, dup_header_row, 1, dup_end_col, NAVY,
        headers=duplicate_headers, amount_columns={"Amount"},
    )
    if len(duplicate_frame):
        dup_last_row = dup_data_row + len(duplicate_frame) - 1
        _format_body_block(ws, dup_data_row, dup_last_row, 1, dup_end_col, NAVY_LIGHT)
        _apply_number_formats(
            ws, duplicate_headers, dup_data_row, dup_last_row, 1,
            {"Amount"}, set(),
        )
        # Only the excess copy is actually excluded from the JE -- the
        # retained canonical row is shown for audit context but must not be
        # styled as if it, too, had been dropped from the accrual.
        for offset, is_excluded in enumerate(duplicate_excluded_flags):
            if is_excluded:
                _apply_duplicate_style(ws, dup_data_row + offset, 1, dup_end_col)
        # Row ID links straight to where this row was originally listed on
        # Reconciliation Detail, so a reviewer never has to search for it by hand.
        row_id_col = duplicate_headers.index("Row ID") + 1
        for offset, qb_id in enumerate(duplicate_frame["Row ID"]):
            _apply_row_id_hyperlink(ws, dup_data_row + offset, row_id_col, qb_id_row_map.get(str(qb_id)))
        if "Reviewer Note" in duplicate_headers:
            note_col = duplicate_headers.index("Reviewer Note") + 1
            for row in range(dup_data_row, dup_last_row + 1):
                ws.cell(row, note_col).protection = Protection(locked=False)
    else:
        dup_last_row = dup_header_row

    _set_widths(ws, 1, dup_end_col, dup_header_row, dup_last_row)
    if "What Was Found" in duplicate_headers:
        ws.column_dimensions[
            get_column_letter(duplicate_headers.index("What Was Found") + 1)
        ].width = 52
    if "Reason" in duplicate_headers:
        ws.column_dimensions[
            get_column_letter(duplicate_headers.index("Reason") + 1)
        ].width = 46
    if "Reviewer Note" in duplicate_headers:
        ws.column_dimensions[
            get_column_letter(duplicate_headers.index("Reviewer Note") + 1)
        ].width = 36
    if len(duplicate_frame):
        dup_note_col = get_column_letter(dup_end_col)
        dup_validation = DataValidation(
            type="textLength", operator="lessThanOrEqual", formula1="1000", allow_blank=True
        )
        dup_validation.error = "Reviewer notes are limited to 1,000 characters."
        dup_validation.errorTitle = "Note too long"
        ws.add_data_validation(dup_validation)
        dup_validation.add(f"{dup_note_col}{dup_data_row}:{dup_note_col}{dup_last_row}")

    # Review Holds: every QuickBooks row the engine could not safely match
    # but that has SOME evidence of a possible Infinium counterpart -- a
    # potential duplicate, a PO already represented, an amount variance, a
    # non-unique or ambiguous candidate, controlled-typo candidates, or a
    # historical clearance blocked by an unresolved historical duplicate.
    # "Cannot safely match" is never treated as "does not exist in
    # Infinium": every row here is excluded from the proposed JE and stays
    # here, visible, until a reviewer records a disposition.
    review_title_row = dup_last_row + 3
    review_caption_row = review_title_row + 1
    review_kpi_label_row = review_title_row + 2
    review_kpi_value_row = review_title_row + 3
    review_header_row = review_title_row + 5
    review_data_row = review_header_row + 1

    review_caption = (
        f"{review_hold_count:,} row(s) are held for review, out of the proposed JE, pending a "
        "documented decision -- see Reason Code for why each was held, and this sheet's embedded "
        "glossary (frozen rows above) for the full explanation of every code. Suggested dispositions: "
        "Release to JE (a confirmed "
        "genuine transaction), Exclude (a confirmed duplicate or already-represented transaction), "
        "Confirm Match (accept a candidate manually), or Carry Forward (needs more investigation)."
        if review_hold_count
        else "No QuickBooks rows are held for review."
    )
    _write_title_band(
        ws, review_title_row, 1, section_end_col,
        "REVIEW HOLDS | EXCLUDED FROM PROPOSED JE - REQUIRES DOCUMENTED DISPOSITION", SLATE,
    )
    _write_caption_band(ws, review_caption_row, 1, section_end_col, review_caption, SLATE)

    review_kpis = [
        ("Items held for review", review_hold_count, ACCOUNTING_COUNT_FORMAT),
        ("Amount excluded from JE", review_hold_amount, ACCOUNTING_CURRENCY_FORMAT),
        ("JE inclusion", "Excluded pending disposition", 'General'),
    ]
    _write_kpi_band(ws, review_kpi_label_row, review_kpi_value_row, review_kpis, review_end_col)

    _write_dataframe_values(ws, review_frame, review_header_row, 1)
    _format_header(
        ws, review_header_row, 1, review_end_col, SLATE,
        headers=review_headers, amount_columns={"Amount", "Infinium Amount", "Difference"},
    )
    review_ref_col = review_headers.index("Referenced Match Ref.") + 1
    review_ref_col_letter = get_column_letter(review_ref_col)
    ws.cell(review_header_row, review_ref_col).alignment = Alignment(
        horizontal="center", vertical="center", wrap_text=True,
    )
    if len(review_frame):
        review_last_row = review_data_row + len(review_frame) - 1
        _format_body_block(ws, review_data_row, review_last_row, 1, review_end_col, SLATE_LIGHT)
        _apply_number_formats(
            ws, review_headers, review_data_row, review_last_row, 1,
            {"Amount", "Infinium Amount", "Difference"}, set(),
        )
        # One consistent amber tint on the Reason cell -- a single disposition
        # (REVIEW HOLD) no longer needs a different color per sub-category.
        reason_col = review_headers.index("Reason") + 1
        row_id_col = review_headers.index("Row ID") + 1
        disposition_col = review_headers.index("Reviewer Disposition") + 1
        review_date_col = review_headers.index("Review Date") + 1
        comment_col = review_headers.index("Comment") + 1
        for offset, record in enumerate(review_frame.to_dict("records")):
            row = review_data_row + offset
            ws.cell(row, reason_col).fill = PatternFill("solid", fgColor=AMBER)
            ws.cell(row, reason_col).alignment = Alignment(wrap_text=True, vertical="center")
            _apply_row_id_hyperlink(ws, row, row_id_col, qb_id_row_map.get(str(record["Row ID"])))
            pointer = record["Referenced Match Ref."]
            if pointer and detail_ref_letter:
                _style_match_ref_link(ws.cell(row, review_ref_col), pointer, detail_ref_letter)
            else:
                ws.cell(row, review_ref_col).alignment = Alignment(horizontal="center", vertical="center")
            for col in (review_date_col, comment_col):
                ws.cell(row, col).protection = Protection(locked=False)
        disposition_validation = DataValidation(
            type="list",
            formula1='"Pending Review,Release to JE,Exclude,Confirm Match,Carry Forward"',
            allow_blank=False,
        )
        disposition_validation.error = "Select a disposition from the list before posting."
        disposition_validation.errorTitle = "Disposition required"
        ws.add_data_validation(disposition_validation)
        disposition_letter = get_column_letter(disposition_col)
        disposition_validation.add(f"{disposition_letter}{review_data_row}:{disposition_letter}{review_last_row}")
        for row in range(review_data_row, review_last_row + 1):
            ws.cell(row, disposition_col).protection = Protection(locked=False)
        comment_validation = DataValidation(
            type="textLength", operator="lessThanOrEqual", formula1="1000", allow_blank=True
        )
        comment_validation.error = "Comments are limited to 1,000 characters."
        comment_validation.errorTitle = "Comment too long"
        ws.add_data_validation(comment_validation)
        comment_letter = get_column_letter(comment_col)
        comment_validation.add(f"{comment_letter}{review_data_row}:{comment_letter}{review_last_row}")
        # Confirm Match asserts this row IS a match -- the reviewer must
        # identify what it matched to (the engine's own related reference, if
        # any, or a documented comment), so an unresolved row can never be
        # marked matched without saying against what.
        related_inf_col = review_headers.index("Related Infinium Row IDs") + 1
        related_inf_letter = get_column_letter(related_inf_col)
        confirm_match_rule = FormulaRule(
            formula=[
                f'AND(${disposition_letter}{review_data_row}="Confirm Match",'
                f'${review_ref_col_letter}{review_data_row}="",'
                f'${related_inf_letter}{review_data_row}="",'
                f'${comment_letter}{review_data_row}="")'
            ],
            fill=PatternFill("solid", fgColor=RED_LIGHT),
        )
        # openpyxl/Excel sqref multi-area syntax is space-separated, not comma-separated.
        confirm_match_range = (
            f"{review_ref_col_letter}{review_data_row}:{review_ref_col_letter}{review_last_row} "
            f"{related_inf_letter}{review_data_row}:{related_inf_letter}{review_last_row}"
        )
        ws.conditional_formatting.add(confirm_match_range, confirm_match_rule)
        # Exclude removes an evidence-backed hold from consideration entirely
        # -- also requires a documented reason.
        review_exclude_rule = FormulaRule(
            formula=[f'AND(${disposition_letter}{review_data_row}="Exclude",${comment_letter}{review_data_row}="")'],
            fill=PatternFill("solid", fgColor=RED_LIGHT),
        )
        ws.conditional_formatting.add(
            f"{comment_letter}{review_data_row}:{comment_letter}{review_last_row}", review_exclude_rule,
        )
    else:
        review_last_row = review_header_row

    review_total_row = review_last_row + 1
    _write_total_row(
        ws, review_total_row, 1, review_end_col,
        {"Amount": float(review_frame["Amount"].sum()) if len(review_frame) else 0.0},
        review_headers, "REVIEW HOLDS TOTAL",
    )
    _add_exception_table(
        ws,
        table_name="ReviewHolds",
        headers=review_headers,
        header_row=review_header_row,
        total_row=review_total_row,
        start_col=1,
        total_label="REVIEW HOLDS TOTAL",
        summed_headers={"Amount"},
        style_name="TableStyleMedium2",
    )

    _set_widths(ws, 1, review_end_col, review_header_row, review_total_row)
    ws.column_dimensions[get_column_letter(review_headers.index("Reason Code") + 1)].width = 26
    ws.column_dimensions[get_column_letter(review_headers.index("Reason") + 1)].width = 52
    ws.column_dimensions[get_column_letter(review_headers.index("Reviewer Disposition") + 1)].width = 22
    ws.column_dimensions[get_column_letter(review_headers.index("Review Date") + 1)].width = 14
    ws.column_dimensions[get_column_letter(review_headers.index("Comment") + 1)].width = 36

    # PO Re-use Error: unlike every section above, these rows are NOT
    # withheld from the accrual -- they already appear, and are already
    # counted, in the QuickBooks exceptions table at the top of this
    # sheet. This section is purely supplementary grouped detail (PO,
    # QuickBooks total, Infinium total, difference, row counts) so a
    # reused PO doesn't read as several unrelated individual exceptions.
    po_reuse_title_row = review_total_row + 3
    po_reuse_caption_row = po_reuse_title_row + 1
    po_reuse_kpi_label_row = po_reuse_title_row + 2
    po_reuse_kpi_value_row = po_reuse_title_row + 3
    po_reuse_header_row = po_reuse_title_row + 5
    po_reuse_data_row = po_reuse_header_row + 1
    po_reuse_section_end_col = section_end_col

    po_reuse_caption = (
        f"{po_reuse_group_count:,} PO(s) appear more than once in the unresolved QuickBooks pool "
        "with a grouped total that does not tie exactly to the grouped Infinium total for the same "
        "PO. A row with Infinium evidence for its PO is held (see the Review Hold section) and is not "
        "accrued; a row with none remains in the exceptions table. This section shows the grouped PO "
        "detail so the pattern is traceable instead of reading as several unrelated exceptions."
        if po_reuse_group_count
        else "No PO Re-use Errors were identified (every PO repeated in the unresolved QuickBooks "
        "pool either ties exactly to Infinium -- and was already matched -- or appears only once)."
    )
    _write_title_band(
        ws, po_reuse_title_row, 1, po_reuse_section_end_col,
        "PO RE-USE ERROR | GROUPED DETAIL - HELD WHEN INFINIUM HAS EVIDENCE", SLATE,
    )
    _write_caption_band(ws, po_reuse_caption_row, 1, po_reuse_section_end_col, po_reuse_caption, SLATE)

    po_reuse_kpis = [
        ("PO groups flagged", po_reuse_group_count, ACCOUNTING_COUNT_FORMAT),
        ("QuickBooks rows involved", po_reuse_qb_row_count, ACCOUNTING_COUNT_FORMAT),
        ("Net difference", po_reuse_net_difference, ACCOUNTING_CURRENCY_FORMAT),
    ]
    _write_kpi_band(ws, po_reuse_kpi_label_row, po_reuse_kpi_value_row, po_reuse_kpis, po_reuse_end_col)

    _write_dataframe_values(ws, po_reuse_frame, po_reuse_header_row, 1)
    _format_header(
        ws, po_reuse_header_row, 1, po_reuse_end_col, SLATE,
        headers=po_reuse_headers,
        amount_columns={"QuickBooks Total", "Infinium Total", "Difference"},
        quantity_columns={"QuickBooks Row Count", "Infinium Row Count"},
    )
    if len(po_reuse_frame):
        po_reuse_last_row = po_reuse_data_row + len(po_reuse_frame) - 1
        _format_body_block(ws, po_reuse_data_row, po_reuse_last_row, 1, po_reuse_end_col, SLATE_LIGHT)
        _apply_number_formats(
            ws, po_reuse_headers, po_reuse_data_row, po_reuse_last_row, 1,
            {"QuickBooks Total", "Infinium Total", "Difference"},
            {"QuickBooks Row Count", "Infinium Row Count"},
        )
    else:
        po_reuse_last_row = po_reuse_header_row

    _set_widths(ws, 1, po_reuse_end_col, po_reuse_header_row, po_reuse_last_row)
    if "Explanation" in po_reuse_headers:
        ws.column_dimensions[
            get_column_letter(po_reuse_headers.index("Explanation") + 1)
        ].width = 52
    if "QuickBooks Row IDs" in po_reuse_headers:
        ws.column_dimensions[
            get_column_letter(po_reuse_headers.index("QuickBooks Row IDs") + 1)
        ].width = 30
    if "Infinium Row IDs" in po_reuse_headers:
        ws.column_dimensions[
            get_column_letter(po_reuse_headers.index("Infinium Row IDs") + 1)
        ].width = 30

    je_title_row = po_reuse_last_row + 3
    je_caption_row = je_title_row + 1
    je_header_row = je_title_row + 2
    je_data_row = je_header_row + 1
    je_headers = [
        "Entry Name", "GL Account", "Account Name", "Debit", "Credit", "Entry Basis",
    ]
    je_frame = pd.DataFrame(
        [
            [
                "AC001 Sales Accrual",
                "017-00000-110160.0",
                "Accrued Income",
                0.0,
                0.0,
                "Unresolved QuickBooks net exception support",
            ],
            [
                "AC001 Sales Accrual",
                "017-91000-400000-0",
                "Income-Manufacturing",
                0.0,
                0.0,
                "Balanced offset",
            ],
        ],
        columns=je_headers,
    )
    _write_title_band(
        ws, je_title_row, 1, section_end_col,
        "PROPOSED JOURNAL ENTRY | AC001 SALES ACCRUAL",
        SLATE,
    )
    _write_caption_band(
        ws, je_caption_row, 1, section_end_col,
        "Post only after review. Debit 017-00000-110160.0 Accrued Income and credit "
        "017-91000-400000-0 Income-Manufacturing for the reviewer-adjusted JE (the engine's TRUE_UNMATCHED "
        "total, plus Review Holds released to JE, less any documented manual exclusions -- see the "
        "Posting Summary bridge); evaluate reversals and negative source values before posting.",
        SLATE,
    )
    _write_dataframe_values(ws, je_frame, je_header_row, 1)
    je_amount_formula = (
        f"=ABS({qb_amount_sum_expr}+{_review_holds_released_expr()}"
        f"-{_je_support_manual_exclusions_expr(result)})"
    )
    ws.cell(je_data_row, 4, je_amount_formula)
    ws.cell(je_data_row, 5, 0.0)
    ws.cell(je_data_row + 1, 4, 0.0)
    ws.cell(je_data_row + 1, 5, je_amount_formula)
    _format_header(
        ws, je_header_row, 1, len(je_headers), SLATE,
        headers=je_headers, amount_columns={"Debit", "Credit"},
    )
    _format_body_block(ws, je_data_row, je_data_row + len(je_frame) - 1, 1, len(je_headers), SLATE_LIGHT)
    for row in range(je_data_row, je_data_row + len(je_frame)):
        for col in (4, 5):
            ws.cell(row, col).number_format = ACCOUNTING_CURRENCY_FORMAT
            ws.cell(row, col).alignment = Alignment(horizontal="right", vertical="center")
    je_total_row = je_data_row + len(je_frame)
    _write_total_row(
        ws, je_total_row, 1, len(je_headers),
        {"Debit": 0.0, "Credit": 0.0}, je_headers, "BALANCED TOTAL",
    )
    ws.cell(je_total_row, 4, f"=SUM(D{je_data_row}:D{je_data_row + len(je_frame) - 1})")
    ws.cell(je_total_row, 5, f"=SUM(E{je_data_row}:E{je_data_row + len(je_frame) - 1})")
    for row in range(je_data_row, je_total_row + 1):
        for col in (4, 5):
            ws.cell(row, col).protection = Protection(locked=True)
    ws.column_dimensions["A"].width = max(ws.column_dimensions["A"].width or 0, 24)
    ws.column_dimensions["B"].width = max(ws.column_dimensions["B"].width or 0, 23)
    ws.column_dimensions["C"].width = max(ws.column_dimensions["C"].width or 0, 28)
    ws.column_dimensions["F"].width = max(ws.column_dimensions["F"].width or 0, 52)

    ws.print_title_rows = "1:4"
    _prepare_sheet(ws)
