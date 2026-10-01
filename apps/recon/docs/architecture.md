# Sales Reconciliation: architecture

Recon reconciles a QuickBooks sales export against an Infinium export for a fiscal period.
It can also use prior-period ("secondary"/historical) files to clear timing differences.

It produces one accounting workpaper, `Sales_Reconciliation_<run>.xlsx`, with five sheets:
Posting Summary, Reconciliation Detail, Unresolved Exceptions, Aggregates, and Raw Data.
An optional legacy-format export, `Sales_Reconciliation_Legacy_<run>.xlsx`, is also available.

## Module map

| Path | Owns |
|---|---|
| `app.py` | Page flow only: uploads, worksheet/header-row selection, mapping, validation, the Run button. |
| `uploads.py` | File hashing, `st.cache_data` wrappers around ingestion, the per-file dataset summary. |
| `ingestion.py` | Reading CSV/XLSX, header detection, subtotal-row filtering, column-mapping panels, source validation. |
| `duplicates.py` | Duplicate detection, classification, and disposition. The lowest layer: imports nothing local. |
| `fuzzy_po_matching.py` | The bounded fuzzy-PO and controlled-typo passes that run after every exact pass. |
| `vendor_aliases.py` + `vendor_aliases.json` | Human-confirmed customer/vendor identity pairs, each with `confirmed_by`, date, and rationale. |
| `matching/` | The matching engine (package; see below). |
| `workpapers/` | Excel workbook builders (package; see below). No Streamlit. |
| `excel_styles.py` | openpyxl styling primitives: borders, bands, number formats, autofit. |
| `ui_components.py` | Streamlit UI blocks: CSS loading, KPI cards, stepper, results tabs, Downloads tab. |
| `utils.py`, `config.py` | Run signature and formatting helpers; palette and time-zone constants. |
| `assets/` | QuickBooks/Infinium logos as data URIs (`ui_assets.py`). The suite logo lives in `shared/assets/`. |
| `tests/` | pytest suite. `conftest.py` holds the shared QB/Infinium mapping and run-metadata fixtures. |
| `data/` | Sample QuickBooks/Infinium exports (real company data, gitignored). |

## `matching/` package, in dependency order

Each module imports only from modules above it. `__init__.py` re-exports the public names, so
callers write `from .matching import build_reconciliation`, not the submodule path.

| Module | Owns |
|---|---|
| `core` | `APP_VERSION`, `MATCHING_RULE_VERSION`, the `MatchGroup` / `ReconciliationResult` types, PO/invoice/amount normalization, `PRODUCT_LEXICON`, fiscal-period parsing, `flag_mask`. |
| `labels` | Section, hold, disposition, and match-reference label constants. |
| `engine` | `prepare_working_frame`, `perform_matching`: every exact, grouped, and fuzzy pass. |
| `exceptions` | Amount variances, ambiguous duplicate candidates, PO reuse errors. |
| `references` | Match reference IDs (`M-001` / `G-001`): assign, carry onto candidates, validate. |
| `holds` | Reason codes and the reference-evidence review holds. |
| `dispositions` | Final QuickBooks dispositions, fuzzy-match review holds, historical clearances. |
| `paired_rows` | The side-by-side paired-row table and per-match assessments. |
| `summaries` | Method, product, and customer summaries; exception analysis; the controls table. |
| `validation` | `validate_reconciliation`: end-of-run integrity checks. |
| `reconciliation` | `build_reconciliation`: runs every step above in order. |

If a new shared constant would create an import cycle, put it in `core` or `labels`.

## `workpapers/` package, in dependency order

| Module | Owns |
|---|---|
| `tables` | Sheet names, Excel table and structured-reference helpers, totals formulas. |
| `finishing` | Row autofit and heights, ignored-errors XML, run metadata, saving workbook bytes. |
| `raw_data` | The Raw Data sheet. |
| `detail` | The Reconciliation Detail sheet, paired display frames, row hyperlinks. |
| `sheet_parts` | Reviewer-facing wording for duplicates, variances, and PO reuse; the KPI band; legends. |
| `unresolved` | The Unresolved Exceptions sheet. |
| `summary_sheets` | The Posting Summary and Aggregates sheets. |
| `legacy` | The legacy-format workbook. |
| `primary` | `build_primary_workbook`: assembles the primary workpaper. |
