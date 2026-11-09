# Period lifecycle, state, and persistence

## Session state keys

| Key | Owner | Contents |
|---|---|---|
| `inventory_layers` | `FIFOLayerStore` | `{alias: [layer, ...]}` — the official current layers. |
| `fifo_period_history` | `FIFOLayerStore` | List of closed-period records, oldest first. |
| `fifo_last_closed_period` | `FIFOLayerStore` | `{fiscal_year, period, period_key, run_signature}` or `None`. |
| `fifo_value_variance` | `FIFOLayerStore` | `{alias: float}` legacy known value variance; no longer updated (engine 2.2.0), kept so old snapshots load. |
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

## Receipt register

Each history record may carry `recognized_receipts` (the receipts that period recognized, as reported, with `recognized_in`,
a `status` of `recognized` or `in opening layers`, and a `late` flag) and `receipt_cutoff`. The register is the union over closed
periods, so `reopen_latest` releases a period's receipts and a restored snapshot restores them with the history.
`receipt_cutoff()` is set by the first period that carries one (and `cutoff_layers()` is that period's beginning layers: the
opening position); records closed earlier carry none. Snapshot schema 3 added these fields; schema 1-2 files still load
(no register).

## Snapshots

- **Location:** `apps/fifo_inventory/fifo_snapshots/`, next to the module rather than the working directory (override with the `FIFO_SNAPSHOT_DIR` env var); one file per period key, and each file is a **full** snapshot (all history).
- **Write:** atomic (write `.json.tmp`, then `replace`), then read back and compare control totals. If the disk write fails *after* the in-memory commit, the UI tells the user to download a manual snapshot.
- **Payload:** `schema_version`, `engine_version`, `created_at_utc`, `last_closed_period`, `period_history`, `product_aliases`, `inventory_layers`, `value_variance`, `control_totals`.
- **`from_json`** rejects newer schema versions, unknown aliases, and control-total mismatches. It accepts legacy files that are just a `{alias: layers}` dict, and converts dict keys inside history back to `int`, since JSON turns them into strings.
- **Manual paths** (all in `sidebar.py`): sidebar download ("Save Current Layers Snapshot"), upload restore, and "Restore a specific saved snapshot from disk".

When changing the snapshot shape: bump `SNAPSHOT_SCHEMA_VERSION` in `user_inputs.py`, keep older files loadable in `from_json`, and treat missing new fields as "not available" (the pattern already used for `product_period_summary` and `value_variance_before`).


## First use: calendar, cutoff, and what protects the opening position

1. **Fiscal calendar (Workspace page, Fiscal calendar):** enter the fiscal year and the first day of Period 1, then **Save
   calendar**. That rebuilds thirteen four-week periods, which is exactly right for FY2027 (Period 1 starts 10/4/2026, Period 13
   ends 10/2/2027). A year whose last period runs longer, such as FY2026 (Period 1 starts 9/28/2025; Period 13 ends 10/3/2026, five
   weeks), needs one more step: edit the Period 13 end date in the table and save again. **Every fiscal year's calendar is kept**
   (`shared/fiscal_settings.json`, gitignored; `calendars` map, with the active year also at the top level so an older single-year
   file loads unchanged). Saving FY2027 leaves FY2026 as it was, including its five-week Period 13; the *Saved fiscal years* list in the
   calendar popover switches which one the editor shows; typing a saved year and its start with the table untouched brings back the saved
   calendar rather than rebuilding it. The FIFO page uses the calendar of the fiscal year it is processing (`calendar_for_year`), and the
   Workspace period-end date resolves against whichever saved year contains it. For a fiscal year with no saved calendar, the FIFO page asks
   for the period end date and takes the receipt window from the day after the last closed period.
2. **The saved Workspace calendar is the authority; the workbook's dates are only a check.** Only a calendar a person saved
   counts (`fiscal_session.verified_calendar`): the placeholder the Workspace shows before any calendar is entered is never used.
   With the operations workbook uploaded, a missing, other-year or internally inconsistent calendar **blocks** processing
   (*Fiscal Calendar Required: Action Required*, with a link to Workspace); nothing is read and no default is substituted
   (`intake.calendar_problem`). When `PERIOD END <year>` row 1 disagrees with the calendar for the selected period, the page
   **warns**, naming both dates, and carries on using the calendar; it does not block. A date exactly a year off is noted as a typo.
   The warning joins the other input warnings (acknowledged with the close checkbox like any other). The separate-upload path, which
   has no workbook dates, still falls back to the entered period end date and the last close.
3. **Receipt cutoff:** before the first close the page shows, next to the Close button, *"First close: this fixes the receipt cutoff
   at <date>"* with the beginning value that becomes the permanent opening position. The date defaults to the day before the first
   period processed and can be changed under *Receipt cutoff* until then. After the close it is fixed (`receipt_cutoff()` ignores any
   later value) and shown as such.
4. **Guards:** *Re-Seed Period 12 Opening Layers* is unavailable while any period is closed; *Reset All FIFO Layers* says it also clears the
   register and cutoff and needs its confirmation box; *Reopen* warns when the period is the first one closed with a register, because
   reopening it releases the cutoff and the opening position. Closing the second and later periods never touches the opening position.
