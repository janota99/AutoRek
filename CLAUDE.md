# Janota Fin Automatations Accounting Apps

One Streamlit app (`streamlit run app.py`, run from this folder) that hosts four accounting
tools for a bottling/packaging operation behind a shared top navigation bar:

| Page (URL) | Code | What it does |
|---|---|---|
| Dashboard (`/`) | `apps/dashboard.py` | Landing page: one card per app, built from the registry in `shared/layout.py`. Every page also carries the account strip (greeting, role, account menu) in its top-right corner. |
| FIFO Inventory (`/fifo-inventory`) | `apps/fifo_inventory/` | 13-period strict FIFO costing of raw materials; PASS/REVIEW/FAIL controls; period close. |
| Sales Reconciliation (`/recon`) | `apps/recon/` | Matches QuickBooks sales to Infinium; builds the reconciliation workpaper. |
| Sales Tax Review (`/sales-tax`) | `apps/sales_tax/` | VBA-macro port: transaction cleanup against a vendor mapping and trial balance, plus vendor-list reconciliation. |
| Invoice Lifecycle Hub (`/invoice-hub`) | `apps/invoice_hub/` | Static HTML/JS prototype of an Outlook invoice tracker, embedded as a Streamlit component. Its page has two tabs: Service overview and My Dashboard (the signed-in person's invoice workspace). |
| Sales page (no URL) | `apps/sales_page.py` | Three-tier pricing page reachable only from the Dashboard's "View Plans" button in the top-right corner (session flag, not a registered page). Placeholder prices; no payment processing. |
| Reviews & Feedback (`/feedback`) | `apps/feedback.py` | Navigation-bar page (not a Dashboard card): the hub's reviews and feedback form, embedded. |

The audience is an accountant/inventory controller, not a developer. Accuracy,
auditability, and "never silently change the books" beat convenience everywhere.

## Run and test

```powershell
pip install -r requirements.txt
streamlit run app.py                          # always from this folder (C:\AutoRek)
py -m pytest                                  # Recon's 355 tests plus the mongodb/ sample-data checks; keep them free of FutureWarnings
node apps/invoice_hub/tests/test_layers.js    # Invoice Hub; prints ALL TESTS PASSED
```

FIFO and Sales Tax have no unit tests. See `docs/suite-architecture.md` for headless page
checks and for how to prove a refactor changed nothing.

## Rules that always apply

- **Never change accounting results silently.** If a cleanup would change a number, a
  match, or a workbook cell, stop and ask. Keep the original behavior and record the issue in
  the app's known-issues list instead.
- **Imports:** page scripts (`apps/<pkg>/app.py`) and tests import absolutely
  (`from apps.recon.matching import …`); every other module imports relatively (`from .matching import …`).
  Three apps have an `ingestion.py`, so a bare `from ingestion import …` would load the wrong one.
- **No `st.set_page_config` in pages; paths come from `Path(__file__)`**, never the working directory.
- **Never commit or publish real data:** `apps/recon/data/`, `apps/sales_tax/data/`, and FIFO's
  `fifo_snapshots/` and `app_settings.json`.
- **Money is compared at the penny** with `Decimal` / signed integer cents, never raw float equality.
- **FIFO:** strict oldest-first consumption, never negative layers. Preview never mutates; only
  `commit_period` changes official layers. Periods close in sequence. Receipt `PRICE` is the
  total extended value. Any snapshot-shape change bumps `SNAPSHOT_SCHEMA_VERSION`; any
  calculation change bumps `ENGINE_VERSION`. The P12 seed values are real opening balances.
- **Recon:** amounts must agree exactly to the signed cent. Ambiguity stays unresolved for review.
  Never add a "closest match" rule. Bump `MATCHING_RULE_VERSION` when match results can change.
- **Sales Tax:** the source and mapping files are read **by position**. Blank ≠ conflict.
  The download stays disabled until the dollar control check is $0.00.
- **Invoice Hub:** sign-in is simulated in the browser; never present it as real access
  control. It also drives the suite's account strip and personalizes only; it never gates an app.
  A new page in `site/` needs the `streamlit-bridge.js` script tag.

## Read before working on… (progressive disclosure)

Load only the doc for the area you're touching. Each app's known-issues list is the first stop
for that app.

| Area | Read |
|---|---|
| Navigation, the shared template, adding an app, cross-app rules, testing/verification | [docs/suite-architecture.md](docs/suite-architecture.md) |
| MongoDB collections (`reviews`, `plans`, `tools`): schemas, sample documents, field types | [mongodb/README.md](mongodb/README.md) |
| **FIFO**: module map, how to verify engine/UI changes | [apps/fifo_inventory/docs/architecture.md](apps/fifo_inventory/docs/architecture.md) |
| FIFO math, layers, usage, variances, vocabulary | [apps/fifo_inventory/docs/fifo-accounting.md](apps/fifo_inventory/docs/fifo-accounting.md) |
| FIFO upload formats, column detection, period parsing, templates | [apps/fifo_inventory/docs/data-inputs.md](apps/fifo_inventory/docs/data-inputs.md) |
| FIFO session state, preview → close → reopen, snapshots, seeding | [apps/fifo_inventory/docs/period-lifecycle.md](apps/fifo_inventory/docs/period-lifecycle.md) |
| FIFO PASS/REVIEW/FAIL checks, tolerances, Excel report layout | [apps/fifo_inventory/docs/controls-and-reporting.md](apps/fifo_inventory/docs/controls-and-reporting.md) |
| FIFO known bugs and doc drift | [apps/fifo_inventory/docs/known-issues.md](apps/fifo_inventory/docs/known-issues.md) |
| **Recon**: module map, the `matching/` and `workpapers/` packages | [apps/recon/docs/architecture.md](apps/recon/docs/architecture.md) |
| Recon matching order, fuzzy/alias rules, duplicates, historical rows, versions | [apps/recon/docs/matching-rules.md](apps/recon/docs/matching-rules.md) |
| Recon known issues | [apps/recon/docs/known-issues.md](apps/recon/docs/known-issues.md) |
| **Sales Tax**: module map, positional rules, verification, known issues | [apps/sales_tax/docs/overview.md](apps/sales_tax/docs/overview.md) |
| **Invoice Hub**: pages, Streamlit embedding, script order, tests | [apps/invoice_hub/docs/overview.md](apps/invoice_hub/docs/overview.md) |

Module docstrings carry the detailed reasoning (especially Recon's `duplicates.py`,
`fuzzy_po_matching.py`, and Sales Tax's `ingestion.py`); the docs above summarize them.

## Project skills

- `/grill-me`: stress-test a plan or design, one question at a time.
- `/handoff [focus]`: write `HANDOFF.md` for a fresh session (user-invoked only).
- `/reup [scope]`: bring the markdown docs back in line with the code; edits docs only (user-invoked only).
