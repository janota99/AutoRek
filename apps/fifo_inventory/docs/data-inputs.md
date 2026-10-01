# Data inputs: Master Grid and Receipts

Both uploads accept `.csv`, `.xlsx`, or `.xlsm`. If a workbook has several sheets,
the UI asks which one to use (`_excel_sheet_names` / `_read_uploaded_table` in
`ingestion.py`). Templates that pass validation as-is come from
`upload_templates.py` (the "Need a starting template?" expander).

## Master Grid (UI label: "Ending Inventory Workbook")

One row per product alias, with a quantity column and a value column for each period.

| Column | Detection |
|---|---|
| Alias | Header `ALIAS` / `PRODUCT ALIAS` (matched ignoring case and punctuation); otherwise the **first column**. Non-numeric alias rows are dropped. |
| Quantity for period *n* | `n`, `0n`, `Pn`, `PDn`, `P.n`, … (1–13), via `parse_period_column_header`. |
| Value for period *n* | The same token with a trailing `V`: `12V`, `PD12V`. |

In app session state the grid is normalized to `PRODUCT ALIAS`, `DESCRIPTION`,
`01`…`13`, `01V`…`13V` (`_blank_master_grid` in `app.py`). An upload is *merged*
into this grid by `apply_updates_to_master_grid`, and only known aliases are applied.

**Errors that block the preview** (collected in `app.py` plus `validate_master_grid`):
- The beginning and ending qty/value columns for the selected period are missing (P1's beginning column is `13`/`13V`).
- A known alias is missing, or an alias appears twice.
- A required cell is blank, non-numeric, or a negative quantity. Value columns *may* be negative.

Unknown aliases only produce a warning.

The "Manual Entry / Paste" expander lets the user edit period cells directly
(alias/description are locked). Any edit changes the run signature, which marks an
existing preview stale.

## Receipts (UI label: "Current Period Receipts")

`prepare_receipts_upload(df, current_period)` upper-cases the headers, then finds each column by candidate names:

| Canonical | Accepted headers | Required |
|---|---|---|
| `QUANTITY` | QUANTITY, QTY, QUANTITY RECEIVED, QTY RECEIVED — else **column 10 by position** | yes |
| `PRICE` (total value) | PRICE, TOTAL COST, TOTAL PRICE, AMOUNT, COST, TOTAL — else **column 11 by position** | yes |
| `PRODUCT ALIAS` | PRODUCT ALIAS, ALIAS, PRODUCT | yes |
| `DATE DELIVERED` | DATE DELIVERED, DELIVERY DATE, DATE RECEIVED, DATE | yes |
| `SOURCE PERIOD` | PERIOD, PD, FISCAL PERIOD | no |
| lineage (optional) | `PO #`, `INVOICE #`, `ITEMS`, `UOM`, `COST BASIS`, `TRANSACTION TYPE` | no |

Numbers may include `$`, thousands commas, and `(parentheses)` for negatives (`_coerce_accounting_series`, `_finite_number`).

**Period filtering:** when there's a period column, `extract_period` pulls 1–13 out
of values like `P12`, `P.12 FYE 2026`, `PD 12`, `12`, and only rows for the
selected period are kept. Rows whose period can't be read are dropped with a
warning. **The fiscal year in the label is not checked.** With no period column,
every row counts as the current period (with a warning).

**Row outcomes:**
- **Rejected (error, blocks close):** unknown alias, qty ≤ 0, value < 0, bad date. Source rows are reported using spreadsheet numbering (header = row 1).
- **Warnings (need acknowledgement to close):** possible duplicates (same alias/date/qty/price/PO/invoice), zero-value receipts, the positional column fallback, rows with an unreadable period.

## Accounting-number parsing

Always use `ingestion._finite_number(value, field_name)` for a single value. It
strips `$` and `,`, turns `(x)` into `-x`, rejects NaN/inf, and raises a
`ValueError` that names the field. Use `_excel_safe_text` for any user text
written back to Excel: it prefixes `= + - @` with `'` to block formula injection.
