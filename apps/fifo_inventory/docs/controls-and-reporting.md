# Controls, tolerances, and the Excel report

## Two control evaluators (both in `excel_export.py`)

**`_compute_summary_control_status(res, ...)`** → `overall_status`, `reconciliation_status`, `valuation_status`, `exception_message`.
This drives the Summary/Executive Summary sheets and, through
`fifo_calculations.summarize_control_counts`, the PASS/REVIEW/FAIL counts that
**gate closing a period**.

**`_compute_product_controls(res, ...)`** → an itemized checklist shown on each product sheet:

| Check | Fails as |
|---|---|
| Processing Integrity | FAIL |
| Beginning Quantity Agreement (layers vs Master Grid) | FAIL — must be 0 units to close |
| Estimated Value Effect of Beginning Qty Variance | REVIEW |
| Beginning Value Variance Consistency (known-variance drift) | REVIEW |
| Receipt Quantity / Value Agreement | FAIL |
| Depletion Quantity Agreement | FAIL |
| Layer Value Conservation (`value_variance`) | FAIL |
| Ending Quantity / Value to Open Layers | FAIL |
| FIFO Chronology | FAIL |
| Unpriced Layer Count (unit cost ≤ zero-cost threshold) | REVIEW |

Overall status order: any FAIL → `FAIL`; else any REVIEW → `REVIEW`; else `PASS - No Activity` (no receipts, no usage, and beginning = ending), `PASS - Zero Balance`, or `PASS`. Code checks with `status.startswith(...)`, so keep those prefixes.

## Materiality helpers

- `_value_is_material(v, tol)`: rounds `v` to cents with `Decimal` ROUND_HALF_UP and compares against `tol` (default $0.01).
- `_quantity_is_material(v, tol)`: `abs(v) > tol` (default `QTY_TOLERANCE` = 0.0001).
- `value_variance_drift_is_acceptable(drift, tolerances)`: **always** uses the fixed ±$0.01 penny rule and ignores `tolerances` on purpose ("monetary materiality is deliberately not configurable").

## Tolerance settings (`app_settings.py`)

`qty_tolerance`, `value_tolerance`, `zero_cost_tolerance`, `drift_min`, `drift_max`
are edited in the ⚙️ Settings tab (`insights.py`) and saved atomically to `apps/fifo_inventory/app_settings.json`
(override the path with `FIFO_SETTINGS_PATH`). `get_effective_tolerances()` reads
the disk on every call, and a corrupt file falls back to the defaults. The
structural guards in `fifo_layer_store.py` (negative-layer rejection, snapshot
control totals) deliberately stay on the fixed constants in `user_inputs.py`.

Closed periods keep their recorded PASS/REVIEW/FAIL counts; changing settings later doesn't rewrite them.

⚠️ Settings are not applied everywhere — see [known-issues.md](known-issues.md).

## Excel workbook (`export_master_excel`)

Built with openpyxl. Sheet order:
1. **Summary** — 16 columns per product: beginning / receipt / usage / ending qty and value, beginning qty variance and its estimated value effect, Reconciliation / Valuation / Overall status, and the exception message. Each product links to its sheet.
2. **Executive Summary** — ending qty and value, depleted value, on-hand and depleted cost per unit, overall status, and exceptions, with a zero-cost footnote.
3. **Input Exceptions** — only when there are run issues (ERROR/WARNING rows from receipts and processing errors).
4. **One sheet per product**, named `A{alias:02d} {short name}` (≤31 chars, `_product_sheet_name`): metrics box, controls block, then the **Current Period Receipts → Inventory On Hand → Inventory Depleted** tables, each with a totals row, plus a link back to the Executive Summary. If $0 legacy layers were consumed, the Depleted total leaves out the average unit cost.

Styling constants (`COLOR_*`, `FONT_*`, `FILL_*`, `FMT_*`) sit at the top of
`excel_export.py` and are reused by `upload_templates.py`. The navy `#0B3350`
matches `styles.css`. Excel table `displayName`s must be unique and use only
valid identifier characters.
