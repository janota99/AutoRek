# Suite architecture: how the combined app is wired

Read this before adding an app, adding a page, changing navigation or the shared
template, or touching anything that crosses app boundaries.

## Request flow

```
streamlit run app.py
  app.py                    set_page_config(wide) → build_navigation() → apply_template() → page.run()
  shared/layout.py          APPS registry, st.navigation(position="top"), st.logo, theme injection
  shared/theme.css          suite chrome + Dashboard cards; injected before each app's own CSS
  apps/dashboard.py         landing page: one card per APPS entry (st.page_link)
  apps/<pkg>/app.py         one page script per application
```

Streamlit runs the root `app.py` on every interaction. That registers all pages, draws the
shared chrome, and then runs the selected page script top to bottom.

## Adding an application

1. Create `apps/<pkg>/` with an empty `__init__.py` and an `app.py` page script.
2. Append an `AppEntry(title, icon, script, url_path, summary)` to `APPS` in `shared/layout.py`.
   The navigation bar and the Dashboard card both come from that list.
3. Give the app a `docs/` folder and add a row to the routing table in `CLAUDE.md`.

## Rules that keep several apps working in one process

- **Imports.** Several apps share module names (FIFO, Recon, and Sales Tax each have an
  `ingestion.py`). A bare `from ingestion import …` would load whichever was imported first.
  - Page scripts (`app.py`) and tests use absolute imports: `from apps.recon.matching import …`.
  - All other modules use relative imports: `from .matching import …`.
- **No `st.set_page_config` in pages.** The root `app.py` owns it. Titles and icons come from the registry.
- **Paths come from `Path(__file__)`**, never the working directory. The server always runs from the repo root.
- **Session state is shared by every page.**
  - Today's keys don't collide; give new keys an app prefix.
  - Streamlit drops a page's widget state, including uploaded files, when the visitor switches
    pages. Plain `st.session_state` values (FIFO's layers, Recon's result) survive the switch.
- **CSS is per page.** An app's injected `<style>` disappears when the visitor leaves its page,
  so it can't leak. Don't hide Streamlit's `header`, because the navigation bar lives in it.
- **`components.declare_component` must be called from an importable module** (see
  `apps/invoice_hub/component.py`). Page scripts are exec'd without a module, so declaring a
  component there raises "module is None".
- **Real company data** lives in `apps/recon/data/`, `apps/sales_tax/data/`, and FIFO's
  `fifo_snapshots/` and `app_settings.json`. The root `.gitignore` excludes them; never commit them.
- `.streamlit/config.toml` forces the light theme because the app stylesheets assume a light page.
- **Dependency pins:** `streamlit>=1.58` (`width="stretch"`, top navigation), `pandas<3`
  (pandas 3 changes string and copy semantics; re-run every check before lifting it), and
  `openpyxl<3.2` (Recon's `excel_styles.py` copies openpyxl's internal `cell._style`; see
  [apps/recon/docs/architecture.md](../apps/recon/docs/architecture.md)).

## Testing and verification

| What | Command (from the repo root) |
|---|---|
| Recon unit tests (355) | `py -m pytest`. `pytest.ini` sets `pythonpath = .` and disables the cache folder. Add `-W error::FutureWarning` to keep the suite free of pandas deprecations. |
| Invoice Hub tests | `node apps/invoice_hub/tests/test_layers.js` (prints `ALL TESTS PASSED`) |
| One page, headless | `PYTHONPATH=. PYTHONIOENCODING=utf-8 py -c "from streamlit.testing.v1 import AppTest; at = AppTest.from_file('apps/<pkg>/app.py', default_timeout=120).run(); print(at.exception)"` |

AppTest caveats:

- Run page scripts directly. `AppTest.switch_page` doesn't follow `st.navigation` routes, and
  `apps/dashboard.py` only works under the root `app.py` (its `st.page_link`s need the registered pages).
- AppTest can't drive `file_uploader`. Selectboxes with a `format_func` (FIFO's product lookup)
  fail under `select_index` and `set_value`.
- Set `FIFO_SNAPSHOT_DIR` to a scratch folder so a headless FIFO run can't write real snapshots.

FIFO and Sales Tax have no unit tests. When restructuring them, prove nothing changed:

- **FIFO:** render the old and new page through the same AppTest interactions and compare every element.
- **Sales Tax:** run the old and new `process_transactions` / `build_workbook` on
  `apps/sales_tax/data/` and compare the frames and every workbook cell.

## Known issues (suite-wide)

- Recon, Sales Tax, and FIFO each keep a few unused parameters in public function signatures.
  Pylint's `unused-argument` warnings there are known; one of them is FIFO known issue #1.
