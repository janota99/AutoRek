# FIFO Inventory: architecture

## Module map

| File | Owns |
|---|---|
| `app.py` | The page, top to bottom: disk recovery, P12 auto-seed, uploads, Master Grid, Batch Processing (preview → close), then the Insights tabs. |
| `sidebar.py` | `render_sidebar(layer_store)`: period selection, closed-period history, snapshots (save/restore), reopen/reset, the P12 re-seed. Returns `(fiscal_year, current_period, period_end_date, selected_key)`. |
| `insights.py` | `render_insights(layer_store)`: the Aging, Trends, Product Lookup, and Settings tabs. Reads official layers and history only. |
| `fifo_calculations.py` | Pure FIFO math: `calculate_fifo`, receipt-layer consolidation, `stage_batch_calculation` across all products, run-signature hashing. No UI. |
| `fifo_layer_store.py` | `FIFOLayerStore`: layers in `st.session_state`, period history, commit/reopen lifecycle, JSON snapshots in `fifo_snapshots/` (next to the module). |
| `ingestion.py` | Parsing and validating the Master Grid and receipts uploads; the `_finite_number` accounting-number parser. |
| `excel_export.py` | PASS/REVIEW/FAIL control rules **and** the Excel workbook builder. Also hosts `_parse_layer_date` / `_check_chronology`. |
| `app_settings.py` | User-editable tolerances persisted to `app_settings.json` (next to the module). |
| `upload_templates.py` | Downloadable blank Master Grid / Receipts templates. |
| `user_inputs.py` | Constants: `PRODUCTS`, the `PERIOD_12_OPENING_LAYERS` seed, versions, default tolerances. |
| `styles.css` | Page styling (navy `#0B3350`, shared with the Excel report). |

Dependencies: everything imports `user_inputs`; `fifo_layer_store` uses `ingestion`;
`fifo_calculations` uses `ingestion`, `excel_export`, and `fifo_layer_store`; `sidebar` and
`insights` sit under `app`; nothing imports `app`. Don't introduce cycles.

## Verifying changes

- **Engine:** call `calculate_fifo(...)` directly from a `py -c` script run at the repo root
  (`from apps.fifo_inventory.fifo_calculations import calculate_fifo`), with a small
  `pandas.DataFrame` of receipts. It has no Streamlit dependency.
- **UI:** headless-run the page (command in `docs/suite-architecture.md`) with `FIFO_SNAPSHOT_DIR`
  pointing at a scratch folder. For a refactor, compare old and new renders element by element.
