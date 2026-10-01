# Period lifecycle, state, and persistence

## Session state keys

| Key | Owner | Contents |
|---|---|---|
| `inventory_layers` | `FIFOLayerStore` | `{alias: [layer, ...]}` — the official current layers. |
| `fifo_period_history` | `FIFOLayerStore` | List of closed-period records, oldest first. |
| `fifo_last_closed_period` | `FIFOLayerStore` | `{fiscal_year, period, period_key, run_signature}` or `None`. |
| `fifo_value_variance` | `FIFOLayerStore` | `{alias: float}` known value variance (working value). |
| `master_grid` | `app.py` | Normalized Master Grid DataFrame. |
| `staged_fifo_run` | `app.py` | The current preview: results, staged layers, Excel bytes, signature. |
| `last_fifo_report` | `app.py` | Excel bytes for the period just closed. |
| `period12_autoseeded`, `disk_snapshot_checked`, `app_flash`, `master_grid_upload_sig/_debug` | `app.py`, `sidebar.py` | One-shot flags and UI messages. |

Streamlit reruns `app.py` from top to bottom on every interaction. Handlers that change state set `app_flash` and call `st.rerun()`.

## Startup order (in `app.py`)

1. `FIFOLayerStore(PRODUCTS)` fills in any missing keys.
2. **Disk recovery, once per session:** if there's no history and no layers, load the newest `fifo_snapshots/*.json` (by modification time). If that load fails, an error is shown and the app is **not** blanked silently.
3. **Automatic seed:** if the selected period is FY`OPENING_SEED_FISCAL_YEAR` (2026) P12, nothing was restored, and the store is empty, load `PERIOD_12_OPENING_LAYERS`. Restore, reset, and manual re-seed all set `period12_autoseeded` so this doesn't run again.

## Preview → Close → Reopen

```
uploads + grid ──► [Calculate Preview] ──► staged_fifo_run (nothing official changes)
                                            │
                    inputs change? ──► run signature differs ──► "stale", close disabled
                                            │
                 [Close FYxxxx-Pnn & Commit] ──► commit_period() ──► snapshot written to disk
                                            │
                    [Reopen Latest] ◄───────┘  (latest period only; restores beginning layers)
```

- **Run signature** (`_build_run_signature`): SHA-256 over engine version, year, period, as-of date, and digests of the Master Grid and receipts. It does **not** include the tolerance settings.
- **Close is blocked if:** the preview is stale, the period is already closed, the period is out of sequence, there are rejected receipt rows, any product had a processing error, `FAIL > 0`, or REVIEW items/warnings haven't been acknowledged (checkbox).
- **`commit_period`** checks sequence (`next_period`: P13 → next year's P1) and appends a history record with `beginning_layers`, `ending_layers`, `ending_control_totals`, `control_counts`, `value_variance_before`, `value_variance`, `product_period_summary` (per-alias rollforward used by Trends/Product Lookup), `run_signature`, and `engine_version`. It then writes to disk.
- **`reopen_latest`** pops the last record, restores its `beginning_layers` and `value_variance_before`, and writes a snapshot named after the new latest period (or `<key>-REOPENED` if history is now empty).
- **Reset** (`reset_all`) wipes everything in memory. It does not delete files on disk.

## Snapshots

- **Location:** `apps/fifo_inventory/fifo_snapshots/`, next to the module rather than the working directory (override with the `FIFO_SNAPSHOT_DIR` env var); one file per period key, and each file is a **full** snapshot (all history).
- **Write:** atomic (write `.json.tmp`, then `replace`), then read back and compare control totals. If the disk write fails *after* the in-memory commit, the UI tells the user to download a manual snapshot.
- **Payload:** `schema_version`, `engine_version`, `created_at_utc`, `last_closed_period`, `period_history`, `product_aliases`, `inventory_layers`, `value_variance`, `control_totals`.
- **`from_json`** rejects newer schema versions, unknown aliases, and control-total mismatches. It accepts legacy files that are just a `{alias: layers}` dict, and converts dict keys inside history back to `int`, since JSON turns them into strings.
- **Manual paths** (all in `sidebar.py`): sidebar download ("Save Current Layers Snapshot"), upload restore, and "Restore a specific saved snapshot from disk".

When changing the snapshot shape: bump `SNAPSHOT_SCHEMA_VERSION` in `user_inputs.py`, keep older files loadable in `from_json`, and treat missing new fields as "not available" (the pattern already used for `product_period_summary` and `value_variance_before`).
