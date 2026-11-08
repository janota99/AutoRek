# FIFO Inventory: architecture

## Module map

| File | Owns |
|---|---|
| `app.py` | The page, top to bottom: disk recovery, P12 auto-seed, the two uploads, results (exceptions, calculation that reruns only when the run signature changes, close), all inside the main tabs Period Processing · Inventory Analysis · History & Snapshots. |
| `workbook_import.py` | Operations' Raw Goods & Receivings workbook, one upload for both sources: finds `RECEIVED LOG` and `PERIOD END <fiscal year>` by name (a hidden duplicate beside one visible sheet is skipped with a note; anything else ambiguous is a problem the page asks about), reads the log by header names and the first table of the period-end sheet, then hands both to `intake.py`. |
| `known_spellings.py` | The spellings operations and the FY2027 tracker use for each material (`KNOWN_SPELLINGS`) and the tracker's Product IDs (`PRODUCT_IDS`, spelled out: they are not alias minus one past ITM-07). |
| `workpaper.py` | The Checks, Period End Summary and Receipts sheets added to the workbook (see controls-and-reporting.md). |
| `materials.py` | `MaterialMatcher`: name/alias/product-ID → material (ambiguity is reported, never guessed); the saved spellings file `material_aliases.json` (gitignored). |
| `intake.py` | Operations' reports → engine inputs: `read_ending_report`, `prepare_period_receipts` (date → period via `period_window`), the exceptions tables. Bump `INPUT_PROCESSING_VERSION` when it can change which rows or quantities reach the engine. |
| `sidebar.py` | `render_sidebar(layer_store)`: period selection and compact last-closed/latest-snapshot status. Returns `(fiscal_year, current_period, period_end_date, selected_key)`. `render_history(...)` draws the History & Snapshots tab: closed-period history, snapshots (save/restore), reopen/reset, the P12 re-seed. |
| `insights.py` | `render_insights(layer_store)`: the Inventory Analysis tab's Aging, Trends, Product Lookup, and Settings tabs. Reads official layers and history only. |
| `fifo_calculations.py` | Pure FIFO math: `calculate_fifo`, receipt-layer consolidation, `stage_batch_calculation` across all products, run-signature hashing. No UI. |
| `fifo_layer_store.py` | `FIFOLayerStore`: layers in `st.session_state`, period history, commit/reopen lifecycle, JSON snapshots in `fifo_snapshots/` (next to the module). |
| `ingestion.py` | Shared parsing helpers and the older alias/period-column Master Grid and receipts parsers (still used by the templates and tests); the `_finite_number` accounting-number parser. |
| `excel_export.py` | PASS/REVIEW/FAIL control rules **and** the Excel workbook builder. Also hosts `_parse_layer_date` / `_check_chronology`. |
| `app_settings.py` | User-editable tolerances persisted to `app_settings.json` (next to the module). |
| `period_status.py` | Pure wording for the top-of-tab strip: which step the period is on and what blocks the next one. `blocks_close` restates the close gate in `app.py`; keep them in step. |
| `upload_templates.py` | Downloadable Master Grid / Receipts templates, prepared for the selected period (prior-period balance from the layers). |
| `user_inputs.py` | Constants: `PRODUCTS`, the `PERIOD_12_OPENING_LAYERS` seed, versions, default tolerances. |

Dependencies: everything imports `user_inputs`; `fifo_layer_store` uses `ingestion`;
`fifo_calculations` uses `ingestion`, `excel_export`, and `fifo_layer_store`; `sidebar` and
`insights` sit under `app`; nothing imports `app`. Don't introduce cycles.

## Verifying changes

- **Tests first:** `py -m pytest apps/fifo_inventory`. `tests/test_golden.py` freezes a multi-product run; a
  failure means a number moved. Regenerate only with `GOLDEN_UPDATE=1` and review the diff, and bump
  `ENGINE_VERSION` for an intended calculation change.
- **Engine:** call `calculate_fifo(...)` directly from a `py -c` script run at the repo root
  (`from apps.fifo_inventory.fifo_calculations import calculate_fifo`), with a small
  `pandas.DataFrame` of receipts. It has no Streamlit dependency.
- **UI:** headless-run the page (command in `docs/suite-architecture.md`) with `FIFO_SNAPSHOT_DIR`
  pointing at a scratch folder. For a refactor, compare old and new renders element by element.
