# FIFO accounting model

How the engine in `fifo_calculations.py` turns beginning layers + receipts + an
ending count into consumption and remaining layers. Read this before changing
any calculation.

## Vocabulary

| Term | Meaning in this codebase |
|---|---|
| **Alias** | Integer product ID (2–30) — the only key used to match products. See `PRODUCTS` in `user_inputs.py`. Names are display-only. |
| **Layer** | One cost lot: a dict with `date`, `qty`, `unit_cost`, `total_value`, plus lineage fields (`layer_id`, `source_period`, `po`, `invoice`, `source_receipts`, …). `total_value` is authoritative; `unit_cost` is always re-derived as `total_value / qty` by `_canonicalize_layer`. |
| **Period key** | `FY{year}-P{nn}`, e.g. `FY2026-P12`. 13 periods per fiscal year. |
| **Beginning layers** | The layers on hand when a period starts = the prior closed period's ending layers (or the Period 12 seed). |
| **Receipts** | Purchases delivered in the period, from the receipts upload. |
| **Usage** | Units consumed in the period: `beginning qty + receipt qty − ending physical qty`. |
| **Depleted** | Audit trail of layers (fully or partially) consumed this period. |
| **On hand** | Layers with quantity left after consumption; these become the next period's beginning layers on close. |
| **Legacy layer** | Opening layer with an unknown/approximate date (e.g. `"Unknown - Legacy Layer"`, `"2022 (Legacy)"`). Its date doesn't parse, so it's skipped by chronology checks and shows as "Unknown" in aging. |

## `calculate_fifo(beginning_layers, receipts_df, ending_qty, ...)`

1. **Validate receipts.** Each row needs a parseable `DATE DELIVERED`, `QUANTITY > 0`, and `PRICE ≥ 0`. `PRICE` is the row's **total value**; `unit_cost = PRICE / QUANTITY`.
2. **Sort receipts** by delivery date, stable on source order (`mergesort`) — ties keep upload order.
3. **Consolidate adjacent receipts** (`_consolidate_adjacent_receipt_layers`): a receipt merges into the *immediately preceding* receipt layer only if unit cost is **exactly** equal and UOM / cost basis / transaction type match. A differently priced receipt in between breaks the run — no merging backward. Merged layers keep every source receipt in `source_receipts` (JSON text) and get a `date_range` like `2026-08-05 through 2026-08-09`. Opening layers are never merged with receipts.
4. **Assign IDs.** Receipt layers: `FY2027-P01-001`, `-002`, …; beginning layers without an ID: `OPEN-001`, …
5. **Chronology check.** `beginning + receipt` layers must be in non-decreasing date order (unparseable dates are skipped). Violation raises `ValueError` → the product FAILs.
6. **Consume oldest first.** Walk the layers in order, consuming `min(layer qty, remaining usage)`:
   - Nothing consumed → full value stays on hand.
   - Fully consumed → the full original value goes to usage (no rounding residue).
   - Partially consumed → usage value = `original_value × consumed / original_qty`; the remainder stays on hand.
7. **Return** `(on_hand, depleted, raw_receipts, metrics)`. `metrics` carries `beg/purch/usage/end` qty and value, `variance_qty`, and `value_variance = beg_val + purch_val − usage_val − end_val` (should be $0.00).

If the ending count is more than beginning + receipts, usage is negative: consumption is set to 0, `variance_qty` records the shortfall, and the summary control FAILs ("Rollforward quantity does not balance"). Negative layers are never created.

## Batch staging — `stage_batch_calculation`

Runs `calculate_fifo` for every alias in `PRODUCTS`, reading from the Master Grid:
- Beginning qty column = previous period (`"11"` for P12); **P1 uses `"13"`** (prior fiscal year's P13).
- Ending qty column = the current period (`"12"`).
- No value column is read. Beginning value is the sum of the stored layers; ending value is derived (below).

Per product it also computes:
- **Beginning quantity variance** = sum of the stored layers − the Master Grid beginning qty. Any nonzero amount (beyond qty tolerance) is a **FAIL and blocks closing**.
- **Ending value is derived, never entered.** It is the value of the layers left on hand after oldest-first consumption (`metrics['end_val']`), and it appears in the Excel report. The Master Grid's value columns (`01V`–`13V`) are optional and ignored. The former "known value variance drift" control compared them with the layers; it was removed in engine 2.2.0 (`ENGINE_VERSION`), so a period can be processed from ending quantities alone. The `fifo_value_variance` figures remain in snapshots only so older ones still load.

A product that raises an exception becomes a `_failed_product_result` (beginning layers passed through untouched, `processing_error` set) instead of aborting the batch.

**Missing layers:** if a product has no stored layers but the Master Grid shows a beginning qty, `sync_beginning` fabricates a single `$0` `"Prior Period Carryover"` layer. That layer is unpriced, so the product lands in REVIEW (Unpriced Layer Count).

## Aging

Layer age = `as_of_date − parsed layer date`. In the UI it's bucketed 0–30 / 31–60 / 61–90 / 90+ / Unknown. `_parse_layer_date` tries a full parse, then pulls the first `MM/DD/YY(YY)` or `YYYY-MM-DD` token out of labels like `"PD11-26: 07/03/26-07/30/26"`.
