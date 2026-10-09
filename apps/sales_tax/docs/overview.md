# Sales Tax Review: overview, rules, known issues

A Streamlit port of a VBA macro with three tools, picked with the segmented control under the page
title: **Transaction Cleanup**, **Vendor Reconciliation**, and **Sales Tax Calculator**.

## Module map

| File | Owns |
|---|---|
| `app.py` | The page: loads its stylesheet (`shared/styles/pages/sales-tax.css`) and switches between the two tools. |
| `transaction_cleanup.py` | The Transaction Cleanup screen: uploads, sidebar options, trial balance cache controls, Run Cleanup, results, new-vendor classification. |
| `cleanup.py` | Transaction Cleanup logic, no Streamlit: `validate_inputs`, GL account building, conflict detectors, `process_transactions`, new-vendor checks, the input fingerprint. Also `clean_key_series`, which Vendor Reconciliation shares. |
| `excel_output.py` | Sheet naming and grouping, the formatted cleanup workbook, the updated mapping workbook. |
| `ui.py` | Markup only: page title, progress stepper, numbered upload-card heading, KPI tiles (styles: `shared/styles/pages/sales-tax.css`, `st-*` classes). Reads no file and changes no number. |
| `vendor_reconciliation.py` | Compares two vendor listings by Vendor ID (Added/Removed/Renamed/Unchanged) and builds an updated 6-column mapping file. |
| `tax_calculator.py` | Sales Tax Calculator logic, no Streamlit: Texas 6.25% State rate, 2.00% local cap (8.25% combined), SaaS 80% taxable base, preset and custom `Location`s, `calculate`, `compare`. `Decimal`; each tax line rounded half-up to the cent. Standalone: reads no upload and feeds no workbook. Tests: `tests/test_tax_calculator.py`. |
| `tax_calculator_ui.py` | The calculator screen: price, location, and sale-type inputs, the summary card, the scenario comparison table, and the custom-location form (kept in session state only). Styles: `stc-*` in `sales-tax.css`. |
| `tax_tape.py` | Draws the compact PNG/PDF "tape" of one calculator result (Pillow) for attaching to invoices as backup. Draws a `TaxResult` that was already computed; recomputes nothing. |
| `ingestion.py` | File reading with size limits, the on-disk trial balance cache, excluded vendor IDs. No Streamlit. |
| `data/` | `trial_balance_cache.xlsx` (the live cache) plus sample input files. Real company data, gitignored. |

Dependency direction:

- `app` → `transaction_cleanup` → `excel_output` → `cleanup`
- `app` → `vendor_reconciliation` → `cleanup`

`transaction_cleanup` also uses `ingestion`. Nothing imports `app.py`, and `cleanup` imports no
other app module.

## Rules

- **The source file is read by position.** The 11 columns are A = Vendor, C = Transaction ID,
  E = Account #, F = Cost Center, G = Amount, J = Code (constants at the top of `cleanup.py`).
- **The mapping file is positional too.** Column 0 = vendor **name** (what transactions match on),
  3 = taxability, 4 = grouping. Keep `vendor_reconciliation.MAPPING_SCHEMA_COLS` in step with
  what `cleanup.py` reads.
- **Blank ≠ conflict.** A blank value on a duplicate row is an incomplete entry. Only two or more
  *different* non-blank values sharing a key are a conflict. `ingestion.py` and `cleanup.py`
  each apply this rule, so change both together.
- **ID vs name normalization.** IDs strip *all* internal whitespace (`clean_key_series`).
  Vendor names only collapse repeated spaces (`clean_name_key_series`), because "ACME CO" and
  "ACMECO" are different names.
- **The download stays disabled** until the dollar control check reconciles to $0.00.
- **GL account padding:** "legacy right-padding" is the default and reproduces the original
  macro. Left padding is the alternative.

## Verifying changes

There are no unit tests. Prove a refactor didn't change output by running old and new
`process_transactions` + `build_workbook` on the files in `data/`. Do it for both padding
modes, with and without `DEFAULT_EXCLUDED_IDS`, and compare the frames, stats, and every
workbook cell. The September 2026 split was verified this way on 104,446 rows.

## Known issues

- The trial balance cache is shared by everyone using the app, with no access control. The
  `ingestion.py` docstring describes it as a convenience cache, not a system of record.
- `cleanup.get_controlled_options` (column index) and `vendor_reconciliation.get_controlled_options`
  (column name) share a name but take different arguments.
