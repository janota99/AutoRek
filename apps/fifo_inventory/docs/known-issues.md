# Known issues and documentation drift

Found while writing these docs (2026-09-30). None of these have been fixed yet.
Confirm with the user before changing behavior — some of it may be intentional.

## Behavior

1. **Drift min/max settings don't do anything.** The Settings tab (`insights.py`) saves `drift_min` / `drift_max`, and `app.py` shows them to the user as the accepted range, but `excel_export.value_variance_drift_is_acceptable` ignores `tolerances` and always applies ±$0.01. Either wire them in or remove them from the UI and captions.
2. **The close gate ignores user tolerances.** `fifo_calculations.summarize_control_counts` calls `_compute_summary_control_status` without `tolerances`, so the PASS/REVIEW/FAIL counts that allow or block **Close** use the built-in defaults, while the Excel report uses the Settings values. The two can disagree.
3. **A stale-preview warning won't fire for settings changes.** The run signature doesn't include tolerances, even though the stale message says "Inputs or settings changed".
4. **The receipt fiscal year isn't checked.** `extract_period` only reads the period number, so a `P12 FYE 2025` row is accepted in FY2026 P12.
5. **`sync_beginning` creates $0 layers.** A product with no layers but a nonzero beginning qty gets an unpriced carryover layer, which shows up only as a REVIEW, not a FAIL.

## Documentation

The old user-facing `README.md` predated the current code and was retired when the
app moved into the combined suite (2026-09-30). Where it disagreed with the code,
the code and these docs are right. It had claimed:
- Aliases 1–15; the real ones are **2–30** (`user_inputs.PRODUCTS`).
- A W0082 rule matching `BLUE`/`NATURAL`/`CLEAR` in the description; no such rule exists. BLUE and NATURAL caps are simply aliases 3 and 4.
- An "Inventory Balances" file with aliases in column A and period headers in row 2. The actual input is the Master Grid (alias column plus `01`–`13` / `01V`–`13V` headers).
- Receipts grouped by "consecutive delivery dates"; the code groups by adjacency in the FIFO sequence with exactly equal unit cost, regardless of date gaps.

## Tooling

- A headless check that runs the whole page, from the repository root: `PYTHONPATH=. PYTHONIOENCODING=utf-8 py -c "from streamlit.testing.v1 import AppTest; at = AppTest.from_file('apps/fifo_inventory/app.py', default_timeout=120).run(); print(at.exception)"`. The UTF-8 setting is needed because the app's emoji crash the Windows cp1252 console. Set `FIFO_SNAPSHOT_DIR` to a scratch folder first if the check shouldn't touch real snapshots.
- No tests yet. `fifo_snapshots/` and `app_settings.json` hold real data and settings; the root `.gitignore` keeps them out of version control.
