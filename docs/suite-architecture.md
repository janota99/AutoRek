# Suite architecture: how the combined app is wired

Read this before adding an app, adding a page, changing navigation or the shared
template, or touching anything that crosses app boundaries.

## Request flow

```
streamlit run app.py
  app.py                    set_page_config(wide) → build_navigation() → apply_template() → page.run()
  shared/layout.py          APPS registry, st.navigation(position="top") (the apps plus Reviews & Feedback),
                            st.logo, theme injection, and the account strip (st.html + shared/suite_banner.js)
  shared/styles/            every stylesheet, in one place (see "Stylesheets" below); base/ is injected before each page's own CSS
  apps/landing.py           Home (/): front landing page, see "Landing page" below
  apps/dashboard.py         Workspace (/workspace): one card per APPS entry (one st.html block)
  apps/sales_page.py        Sales page (3 purchase tiers); NOT a registered page, drawn by the Dashboard only
  apps/feedback.py          Reviews & Feedback page: the Invoice Hub's feedback.html, embedded
  apps/dashboard.js         its behavior (spotlight, entrance, animated Recon logo, client-side nav); styling is styles/pages/dashboard.css
  apps/<pkg>/app.py         one page script per application
```

Streamlit runs the root `app.py` on every interaction. That registers all pages, draws the
shared chrome, and then runs the selected page script top to bottom.

## Sign-in, the invoice workspace, and the account strip

Sign-in is the Invoice Hub's **simulated, browser-only** sign-in. It personalizes; it restricts
nothing, and every application stays open to anyone. Never describe it as access control.

- The Invoice Lifecycle Hub page has two tabs inside the embedded hub: Service and My Dashboard
  (the invoice workspace). Signed out, My Dashboard is the sign-in panel. Reviews & Feedback is its
  own page in the navigation bar (`apps/feedback.py`, `invoice_hub(page="feedback.html")`); it needs no sign-in.
- The hub keeps the signed-in email in sessionStorage (`invoiceHub.session.v1`). Its
  `streamlit-bridge.js` copies name, role line, and intro to `pp.suite.profile.v1` and posts a
  `pp-suite:session` message to the page. Same origin, same tab, so the pages share both keys.
- `apply_template()` draws the account strip on every page, fixed in the top-right of the navigation
  bar's row so it takes no room from a page; `shared/suite_banner.js` fills it from those keys.
  Signed in: a greeting (US Central time, like the hub), the role, and an avatar menu with the
  profile, **My invoice workspace** (opens the hub on My Dashboard), and **Sign out** (clears both
  keys). Signed out: a **Sign in** button that opens the same place. Because sessionStorage survives
  page switches, the same person follows you through every application; the Reviews & Feedback form
  starts with their name and role.
- The strip asks the hub for a page through sessionStorage `pp.suite.hubPage` (read once by
  `streamlit-bridge.js` on the hub's first render). A CSS rule in `styles/base/chrome.css` narrows Streamlit's
  nav overflow so a page link is never covered by the strip.
- Scripts inlined through `st.html` must not contain `<` followed by a letter, even in a comment:
  Streamlit's sanitizer drops the whole script. `suite_banner.js` avoids `<` entirely.

## Landing page (Home, `/`)

`apps/landing.py` is the default page. Sections 1-4 (navigation, opening, solution explorer, walkthrough) and 7-8 (FAQ and
background, closing) are `st.html` blocks styled by `styles/pages/landing.css` and driven by `apps/landing.js`; the JS
delegates its handlers from `document` and binds once, because the page is several blocks. Deliverables and plans are
Streamlit widgets, since they download files and hold purchase state.

- **Numbers.** `apps/landing_data.py` computes every example figure in integer cents from small synthetic row lists
  (reconciliation counts, FIFO cost, the transaction control), and builds the three sample CSV downloads from the same
  rows. `shared/tests/test_landing.py` checks they tie out. It does not call the real engines, so change both if a
  workflow's rules change.
- **Demo.** Each solution tab opens on its populated Results view; **Inspect inputs** shows the source rows and
  **Rerun example** replays the animation. **Try This Workflow** links to the real tool at `?demo=1`.
- **Available vs Planned.** The walkthrough (`WALKTHROUGH` in `landing_data.py`) labels every capability. Keep Planned items
  honest: nothing listed there is built.
- **Purchase sequence** (session state `pp_l_*`): plan and interval, review and total, account, simulated checkout,
  confirmation, onboarding. It reuses `apps/sales_page.py` (`TIERS`, `totals`, `order_summary`, `payment_section`,
  `contact_dialog`) and `shared/billing.py`, so prices and card checks live in one place. The account is a session-only
  demo account with **no password**; checkout records "Demo: not charged" and discards card details. Onboarding's mapping
  step is a preview: each tool still maps your own file when you upload it. **Enter the workspace** calls
  `st.switch_page(..., query_params={"demo": "1"})`.
- The older Dashboard-only Plans page (View Plans button) still exists and shares the same helpers.
- The "Where it came from" text (`BACKGROUND`) is a draft written from the repository's history; replace it with the
  owner's own account.

## Dashboard layout

The Dashboard is one `st.html` block: a left rail (Overview, Applications; Projects, Reports and Settings are greyed
"Soon" placeholders with no page behind them) beside the workspace: a Get started panel, the application cards, and a
"How your data is handled" drawer. Each card has **Open workspace**, **Try demo**, and **View details** (supported files,
inputs, outputs, limitations, and plan availability, from `AppEntry` in `shared/layout.py` and `TIERS`).

- **Try demo** links to `/<app>?demo=1`. `shared/sample_data.py:_seed_demo` loads each uploader's sample once per session
  when it sees that parameter, so the workflow opens populated. It reuses the existing sample files and builders; nothing
  else changes, and FIFO still disables Close & Commit while a sample is loaded.
- **There is no saved-project store, account, or billing system.** The stats strip says "Demo access" and links to the Plans
  page. No fiscal-period selector lives on the Dashboard: each application sets its own period.
- `_DATA_HANDLING` in `apps/dashboard.py` states what is stored where (session uploads, in-memory Excel outputs, and the few
  files written to the server's disk). Update it if an app starts storing something new.
- **Demo checkout and charge register** (Plans page). After choosing Starter or Professional, a Payment section offers card
  (name, number, MM/YY, CVC, ZIP), gift card, or PayPal. `shared/billing.py` validates (Luhn, expiry, CVC length) and keeps
  only brand and last four digits; the form clears on submit. A passing order adds a row to the session-only charge register
  marked "Demo: not charged". No processor is connected. A real checkout must use a processor's hosted card fields so card
  numbers never reach this app; replace `_payment_section` then.
- The Plans page adds a comparison table (`_comparison_html` in `apps/sales_page.py`). Rows marked "To be confirmed" are
  undecided business terms; the app enforces no plan limits.

## Sales page (Dashboard-only)

`apps/sales_page.py` holds the three tiers (Starter, Professional, Enterprise) and their placeholder
prices (`TIERS`, edit there). It is deliberately **not** passed to `st.navigation`, so it has no URL
and no navigation-bar entry. The Dashboard's "View Plans" button in the top-right corner sets
`st.session_state["pp_view"]`; `apps/dashboard.py` then draws the sales page instead of the cards.
`app.py` clears the flag whenever another page is selected, so a fresh visit, a direct URL, or
returning from another app lands on the normal Dashboard. Choosing a plan only shows an order
summary: no payment processor is connected and nothing is charged.

## Sample data

`sample_data/` holds synthetic files (made-up vendors, customers, POs, amounts; never real data) in each
tool's expected layout. `build_samples.py` regenerates them. `shared/sample_data.py` provides
`sample_uploader` (a file uploader plus a "Use Sample Data" button; a real upload always wins) and
`sample_downloads` (the "Download Sample Templates" drawer). Wired into Sales Tax (Transaction Cleanup,
Vendor Reconciliation), Recon (all four uploads) and FIFO. FIFO's samples are not files: they are built
from the layers in the session (`apps/fifo_inventory/sample_data.py`) so they reconcile, and Close & Commit
is disabled while one is loaded, so demo numbers can never become the official layers. The Sales Tax trial balance is download-only, because the real one is
a shared on-disk cache. Recon reads QuickBooks column A as the fiscal period, so its sample keeps one there.

## Hover previews and the MongoDB samples

The Dashboard cards and the Plans page cards each fill a preview bar on `mouseenter` and clear it on `mouseleave`
(`apps/dashboard.js`; the script in `apps/sales_page.py`). Their data is not hard-coded in the script: a card's inputs
and outputs come from `AppEntry` in `shared/layout.py`, and a plan's tools from `Tier.tool_ids`, both passed as JSON
data attributes. `mongodb/` holds the matching collections (`reviews`, `plans`, `tools`) with `$jsonSchema`
validators and sample documents, and `mongodb/test_samples.py` fails if they drift from `TIERS` or `APPS`.
The site is not connected to MongoDB.

## Adding an application

1. Create `apps/<pkg>/` with an empty `__init__.py` and an `app.py` page script.
2. Append an `AppEntry(title, icon, script, url_path, summary)` to `APPS` in `shared/layout.py`.
   The navigation bar and the Dashboard card both come from that list.
3. Give the app a `docs/` folder and add a row to the routing table in `CLAUDE.md`.
4. Put its CSS in `shared/styles/pages/<name>.css`, add the name to `PAGES` in `shared/styles/__init__.py`, and call
   `inject_page("<name>")` once from the page script.

## Stylesheets

All CSS lives in `shared/styles/`; `shared/styles/__init__.py` is the only code that reads it.

| Folder | Files | Loaded |
|---|---|---|
| `base/` | `tokens.css` (design tokens, `--pp-*`), `chrome.css` (account strip, sidebar logo, captions, "View Plans" pill, top-bar logo), `status.css` (PASS/REVIEW/FAIL badges and tiles) | on every page by `apply_template()`, in that order |
| `pages/` | `dashboard.css`, `sales-page.css`, `fifo.css`, `recon.css`, `sales-tax.css` | by that page, after the base, so a page can override it |

- Pages call `inject_page("<name>")`; pages drawn with `st.html` (Dashboard, Sales page) embed `style_tag("<name>")`.
- Cascade order is injection order. Don't reorder `BASE_FILES`, and keep each page file's own overrides last.
- New colors go in `tokens.css` as `--pp-*` and are used with `var(...)`. The apps' own palettes (`--rec-*`,
  `--accent`) are still per page; unifying them changes how the apps look, so do it deliberately.
- The Invoice Hub is the one exception: it runs in an iframe that can't see this CSS, so it keeps
  `apps/invoice_hub/site/styles.css`.

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
| Recon unit tests (418) | `py -m pytest`. `pytest.ini` sets `pythonpath = .` and disables the cache folder. Add `-W error::FutureWarning` to keep the suite free of pandas deprecations. |
| Invoice Hub tests | `node apps/invoice_hub/tests/test_layers.js` (prints `ALL TESTS PASSED`) |
| One page, headless | `PYTHONPATH=. PYTHONIOENCODING=utf-8 py -c "from streamlit.testing.v1 import AppTest; at = AppTest.from_file('apps/<pkg>/app.py', default_timeout=120).run(); print(at.exception)"` |

AppTest caveats:

- Run page scripts directly. `AppTest.switch_page` doesn't follow `st.navigation` routes, and
  `apps/dashboard.py` only works under the root `app.py` (its card links are routed through the registered pages' nav links).
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
