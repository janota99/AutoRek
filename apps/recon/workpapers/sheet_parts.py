"""Reviewer-facing wording for duplicates, variances, and PO reuse, plus the KPI band and legends."""

from __future__ import annotations

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill, Protection

from ..config import (
    AMBER,
    DUPLICATE_RED_FILL,
    DUPLICATE_RED_TEXT,
    FONT_NAME,
    GREEN_LIGHT,
    NAVY,
    RED_LIGHT,
    SLATE,
    SLATE_LIGHT,
    TEXT,
)
from ..duplicates import (
    DUPLICATE_BASIS_CROSS_SCOPE,
    DUPLICATE_BASIS_INVOICE_ONLY,
    DUPLICATE_BASIS_PO_ONLY,
    DUPLICATE_BASIS_STRICT,
)
from ..excel_styles import _thin_border, fix_row_height
from ..matching import REASON_CODE_GLOSSARY
from ..utils import format_currency


_DUPLICATE_STATUS_LABELS = {
    "Retained canonical row": "Kept (Original)",
    "Excluded excess copy": "Removed (Duplicate)",
    "Held for review - excluded from proposed JE pending disposition": "Pending Review",
}


_DUPLICATE_BASIS_PHRASES = {
    DUPLICATE_BASIS_STRICT: "PO, Invoice, and Amount",
    DUPLICATE_BASIS_PO_ONLY: "PO and Amount",
    DUPLICATE_BASIS_INVOICE_ONLY: "Invoice and Amount",
    DUPLICATE_BASIS_CROSS_SCOPE: "PO, Invoice, and Amount across periods",
}


def _what_was_found(row: dict) -> str:
    basis = row.get("Duplicate Basis", "")
    po = row.get("Normalized PO") or ""
    invoice = row.get("Normalized Invoice") or ""
    amount_str = format_currency(row.get("Amount"))
    if basis == DUPLICATE_BASIS_PO_ONLY:
        shared = f"Same PO {po} and Amount {amount_str} (Invoice blank on both rows)"
    elif basis == DUPLICATE_BASIS_INVOICE_ONLY:
        shared = f"Same Invoice {invoice} and Amount {amount_str} (PO blank on both rows)"
    elif basis == DUPLICATE_BASIS_CROSS_SCOPE:
        shared = f"Same PO {po}, Invoice {invoice}, and Amount {amount_str} as a row in the other period's data"
    else:
        shared = f"Same PO {po}, Invoice {invoice}, and Amount {amount_str}"
    other_ids = [
        piece.strip() for piece in str(row.get("Other Source Row IDs In Group", "")).split(";")
        if piece.strip()
    ]
    if not other_ids:
        as_clause = ""
    elif len(other_ids) == 1:
        as_clause = f" as row {other_ids[0]}"
    elif len(other_ids) <= 3:
        as_clause = f" as rows {', '.join(other_ids)}"
    else:
        as_clause = f" as rows {', '.join(other_ids[:3])}, and {len(other_ids) - 3} more"
    return f"{shared}{as_clause}."


def _duplicate_reason(row: dict) -> str:
    basis_phrase = _DUPLICATE_BASIS_PHRASES.get(row.get("Duplicate Basis", ""), "PO, Invoice, and Amount")
    if row.get("Payload Confirmed"):
        return "These rows appear identical in every field compared."
    differing = str(row.get("Differing Confirmation Fields", "") or "").strip()
    if differing:
        return f"These rows match on {basis_phrase}, but differ in: {differing.replace('; ', ', ')}."
    return f"These rows share the same {basis_phrase}; no other confirmation fields were available to compare."


def _simplify_duplicate_display(frame: pd.DataFrame) -> pd.DataFrame:
    """Reduce a duplicate_analysis-shaped frame to a small, plain-English view
    for non-technical reviewers -- unique ID, what was found, and why.

    This is purely a display transform for the "Unresolved Exceptions" sheet.
    The full technical schema (Screening Stage, Duplicate Basis, Normalized
    PO/Invoice, Confirmed Copy Set ID, etc.) stays intact everywhere else --
    the "QuickBooks/Infinium Duplicates" audit sheets, `finalize_review_
    dispositions`, and `validate_reconciliation` all keep reading the
    original `duplicate_analysis` frame untouched.
    """
    optional_columns = [col for col in ("Reviewer Note", "Reviewer Disposition") if col in frame.columns]
    if frame.empty:
        return pd.DataFrame(columns=["Duplicate ID", "Row ID", "What Was Found", "Reason", "Amount", "Status"] + optional_columns)
    records = frame.to_dict("records")
    simplified = pd.DataFrame({
        "Duplicate ID": frame["Duplicate Group ID"].values,
        "Row ID": frame["Source Row ID"].values,
        "What Was Found": [_what_was_found(row) for row in records],
        "Reason": [_duplicate_reason(row) for row in records],
        "Amount": frame["Amount"].values,
        "Status": frame["Disposition"].map(_DUPLICATE_STATUS_LABELS).fillna(frame["Disposition"]).values,
    })
    for column in optional_columns:
        simplified[column] = frame[column].values
    return simplified


_AMOUNT_VARIANCE_WHAT_MATCHED = {
    "High-likelihood amount variance": "Same PO and Invoice",
    "Critical possible sign reversal": "Same PO and Invoice",
    "Strong invoice-linked amount variance": "Same Invoice only",
    "PO-linked amount variance": "Same PO only",
}


def _amount_variance_reason(row: dict) -> str:
    if row.get("Possible Sign Reversal"):
        return (
            "Same magnitude, opposite sign -- check whether one system recorded this "
            "as a credit and the other as a debit before treating it as a plain typo."
        )
    return (
        "References agree but the dollar amount does not -- most likely a data-entry "
        "error on one side. Verify against source documents; do not accrue either "
        "amount until it's resolved."
    )


def _simplify_po_reuse_display(frame: pd.DataFrame) -> pd.DataFrame:
    """Reduce a po_reuse_errors-shaped frame (see build_po_reuse_errors in
    matching/exceptions.py) to the reviewer-facing grouped detail columns -- plain-
    English headers, and drops the internal row-index columns used only
    for lookups elsewhere. The full technical schema (Normalized PO,
    QuickBooks/Infinium Row Indexes, etc.) stays intact on
    result.po_reuse_errors for validate_reconciliation and any future
    audit-sheet use."""
    columns = [
        "PO Reuse ID", "PO", "QuickBooks Row IDs", "QuickBooks Row Count",
        "QuickBooks Total", "Infinium Row IDs", "Infinium Row Count", "Infinium Total",
        "Difference", "Explanation",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    simplified = frame.rename(columns={"Normalized PO": "PO"})
    return simplified.reindex(columns=columns)


def _write_kpi_band(
    ws, label_row: int, value_row: int, kpis: list[tuple], end_col: int, start_col: int = 1,
) -> None:
    """Render a row of KPI cards packed tightly at the left: each card
    merges two columns so its label and value keep a fixed, compact width
    regardless of how wide the data columns beneath happen to be, and
    cards sit directly against each other with no empty gap column between
    them -- unlike spacing cards across every-other column of a wide data
    table, which stretches the band across the full sheet width."""
    col = start_col
    for label, value, number_format in kpis:
        if col + 1 > end_col:
            break
        ws.merge_cells(start_row=label_row, start_column=col, end_row=label_row, end_column=col + 1)
        ws.merge_cells(start_row=value_row, start_column=col, end_row=value_row, end_column=col + 1)
        ws.cell(label_row, col, label)
        ws.cell(value_row, col, value)
        ws.cell(label_row, col).font = Font(name=FONT_NAME, size=9, bold=True, color=SLATE)
        ws.cell(value_row, col).font = Font(name=FONT_NAME, size=12, bold=True, color=NAVY)
        # A card is a fixed, compact block: a long label or value shrinks to
        # fit its card instead of wrapping and stretching the row.
        ws.cell(label_row, col).alignment = Alignment(horizontal="left", vertical="center", shrink_to_fit=True)
        ws.cell(value_row, col).alignment = Alignment(horizontal="left", vertical="center", shrink_to_fit=True)
        ws.cell(value_row, col).number_format = number_format
        ws.cell(value_row, col).protection = Protection(locked=True)
        for row in (label_row, value_row):
            for c in (col, col + 1):
                ws.cell(row, c).fill = PatternFill("solid", fgColor=SLATE_LIGHT)
                ws.cell(row, c).border = _thin_border()
        col += 2
    fix_row_height(ws, label_row, 15)
    fix_row_height(ws, value_row, 21)


# Status-fill meanings used across this sheet -- a swatch and a short label
# per entry, so a reviewer opening the file cold doesn't need tribal
# knowledge of what each color means. Deliberately a strict traffic-light
# palette (green/amber/red) plus one distinct duplicate-red, matching every
# fill actually painted on this sheet -- no pastel variants that don't map
# to a real status here. The first three name the period classes exactly as
# the fiscal-period summary does; every one of them is also spelled out as
# plain text in that summary's own "Period Classification" column, so a
# reviewer never has to infer meaning from color alone.
# (fill, text_color_or_None, label)

_STATUS_COLOR_LEGEND: list[tuple] = [
    (GREEN_LIGHT, None, "Current Period"),
    (AMBER, None, "Prior Period / pending review"),
    (RED_LIGHT, None, "Urgent Prior Period"),
    (DUPLICATE_RED_FILL, DUPLICATE_RED_TEXT, "Confirmed duplicate - excluded from JE"),
]


def _write_color_legend(ws, first_row: int, start_col: int, end_col: int) -> int:
    """A compact color key: a small filled swatch immediately followed by
    its label, packed left to right with no gap between entries -- same
    tight-packing idea as _write_kpi_band, applied to a legend instead of a
    KPI card. Each entry takes four columns (swatch plus a three-column
    label), as many per row as the sheet is wide, wrapping onto a second
    row rather than dropping entries. Returns the last row used."""
    per_row = max(1, (end_col - start_col + 1) // 4)
    last_row = first_row
    for index, (fill_color, text_color, label) in enumerate(_STATUS_COLOR_LEGEND):
        row = first_row + index // per_row
        col = start_col + (index % per_row) * 4
        last_row = row
        swatch = ws.cell(row, col)
        swatch.fill = PatternFill("solid", fgColor=fill_color)
        swatch.border = _thin_border()
        label_end = min(col + 3, end_col)
        if label_end > col + 1:
            ws.merge_cells(start_row=row, start_column=col + 1, end_row=row, end_column=label_end)
        label_cell = ws.cell(row, col + 1, label)
        label_cell.font = Font(name=FONT_NAME, size=8, italic=True, color=text_color or TEXT)
        label_cell.alignment = Alignment(horizontal="left", vertical="center", shrink_to_fit=True)
    for row in range(first_row, last_row + 1):
        fix_row_height(ws, row, 15)
    return last_row


def _write_reason_code_legend(ws, first_row: int, start_col: int, end_col: int, codes: list[str]) -> int:
    """A compact reason-code glossary, scoped to only the codes actually
    appearing in this run's Review Hold table below -- not the full static
    glossary of every code the engine can ever produce. Replaces the
    dedicated Reason Code Glossary sheet: one row per code, bold code
    followed by its plain-language definition, in the sheet's own frozen
    top rows instead of a lookup one tab away. Returns the last row used
    (unchanged from first_row - 1 if there are no codes to show)."""
    if not codes:
        return first_row - 1
    title_row = first_row
    ws.cell(title_row, start_col, "REASON CODES ON THIS SHEET")
    ws.cell(title_row, start_col).font = Font(name=FONT_NAME, size=9, bold=True, color=SLATE)
    last_row = title_row
    for code in codes:
        last_row += 1
        description = REASON_CODE_GLOSSARY.get(code, "No further explanation is on file for this code.")
        if end_col > start_col:
            ws.merge_cells(start_row=last_row, start_column=start_col, end_row=last_row, end_column=end_col)
        cell = ws.cell(last_row, start_col)
        cell.value = f"{code}:  {description}"
        cell.font = Font(name=FONT_NAME, size=8, color=TEXT)
        cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        cell.fill = PatternFill("solid", fgColor=SLATE_LIGHT)
        fix_row_height(ws, last_row, 26)
    return last_row
