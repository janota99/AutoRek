# AutoRek: Panhandle Pure Accounting Apps

AutoRek collects accounting tools for a bottling and packaging operation in one
[Streamlit](https://streamlit.io) web app. It runs on your own computer: you open it in a
browser, upload your exports, review the results on screen, and download Excel workpapers.

| Tool | What it does |
|---|---|
| **FIFO Inventory** | Costs raw materials with strict first-in, first-out (FIFO) layers across a 13-period fiscal year. Runs PASS/REVIEW/FAIL controls and closes periods in order. |
| **Sales Reconciliation** | Matches QuickBooks sales to Infinium sales by PO, invoice, and exact signed amount, then builds the reconciliation workpaper. |
| **Sales Tax Review** | Cleans up sales-tax transactions against a vendor mapping and the trial balance, and reconciles two vendor listings. |
| **Invoice Lifecycle Hub** | Prototype of an Outlook invoice tracker that routes invoices and bills into approval queues. It uses demo data only. |

The tools are built for accuracy and auditability: amounts are compared to the cent, unclear
items are flagged for review instead of guessed, and nothing changes the official books until
you confirm it.

---

## Setup

### What you need

- **Python 3** (developed and tested on Python 3.13). On Windows, install it from
  [python.org](https://www.python.org/downloads/) and tick *"Add python.exe to PATH"*.
- **Git**, to download the code.
- **Node.js** (optional). It's only needed to run the Invoice Hub's automated tests.

### Install

Open PowerShell and run:

```powershell
git clone https://github.com/janota99/AutoRek.git
cd AutoRek

# Optional but recommended: give the app its own Python environment
py -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

On macOS or Linux, use `python3` instead of `py`, and run `source .venv/bin/activate` to
activate the environment.

### Run

Always run the app **from the repository folder**:

```powershell
streamlit run app.py
```

The app opens in your browser at <http://localhost:8501>. Press `Ctrl+C` in the terminal to
stop it. If you set up a virtual environment, activate it again (`.\.venv\Scripts\Activate.ps1`)
each time you open a new terminal.

---

## Using the app

The app opens on a **Dashboard** with one card per tool. Use the navigation bar at the top to
switch tools. Uploaded files are cleared when you switch to another tool, but finished results
(FIFO layers and the latest reconciliation) stay available until you close the browser session.

### FIFO Inventory

Tracks raw-material cost layers through a 13-period fiscal year. Consumption is always taken
from the oldest layer first, and a layer can never go negative.

1. **Choose the fiscal year and period** in the sidebar. Periods must be closed in order:
   P13 is followed by the next year's P1.
2. **Upload the Ending Inventory Workbook (Master Grid).** It has one row per product alias,
   with a quantity column and a value column for each period (for example `12` and `12V`). It
   can be a `.csv`, `.xlsx`, or `.xlsm` file.
3. **Upload the Current Period Receipts.** Each receipt needs a product alias, a delivery
   date, a quantity, and `PRICE`, which is the **total extended value** of the line, not the unit
   price.
   *Not sure about the format? Open "Need a starting template?" to download templates that
   pass validation as they are.*
4. **Click "Calculate Preview & Generate Master Report".** The preview changes nothing
   official. It shows PASS/REVIEW/FAIL controls for each product and an Excel report you can
   download.
5. **Close the period.** "Close FYxxxx-Pnn & Commit Ending Layers" stays disabled until there
   are no FAIL results and no rejected receipt rows, and you've acknowledged any REVIEW items.
   Closing saves a snapshot to disk automatically.

The sidebar can also save or restore a layer snapshot, reopen the most recently closed period,
and reset all layers. The **Period 12 opening layers** are the real opening balances the
tracker starts from.

### Sales Reconciliation

Reconciles a QuickBooks sales export against an Infinium sales export for a fiscal period.

1. **Upload the current QuickBooks export, then the current Infinium export.** Each can be a
   CSV or Excel file.
2. **(Optional) Upload the prior-period files.** They can clear timing differences that cross
   periods.
3. **Confirm the worksheet, the header row, and the column mapping** for each file. The app
   detects them automatically, and you can override its choices.
4. **Click "Run Reconciliation".**
5. **Review the results tabs, then download the workpaper** (`Sales_Reconciliation_<run>.xlsx`).
   It has five sheets: Posting Summary, Reconciliation Detail, Unresolved Exceptions,
   Aggregates, and Raw Data. A legacy-format export is also available.

The matching rules are run in this order:

1. PO + invoice + exact amount
2. PO + exact amount
3. Invoice + exact amount
4. Small grouped totals
5. Tightly limited fuzzy PO matching, which is always held for review

Amounts must agree **to the exact signed cent**. When a row could match more than one thing,
the app leaves it unresolved and lists it as an exception instead of guessing. Confirmed
customer-name aliases (for example, a surname in one system and a first name in the other) are
recorded in `apps/recon/vendor_aliases.json`, each with who confirmed it and why.

### Sales Tax Review

A port of an Excel VBA macro. Use the sidebar to choose between its two tools.

**Transaction Cleanup**

1. **Upload the Source Transactions file** (an 11-column `.xlsx`) and the **Vendor Mapping**
   file. Both are read **by column position**, so keep the column order the same as the
   standard exports.
2. **Provide the Trial Balance file.** After the first upload it's cached on the server, so you
   only replace it when it changes.
3. **(Optional) Choose options** such as the GL account padding mode or an exclusion list.
4. **Click "Run Cleanup"**, then review the summary and the preview tabs.
5. **Download the workbook.** The download stays disabled until the dollar control check
   reconciles to **$0.00**.
6. **If new vendors appear,** classify them on screen and click "Build Updated Mapping File".

**Vendor Reconciliation**

Upload the previous and updated vendor listings, map their columns, and click
**"Compare Listings"**. The app lists Added, Removed, and Possible Renamed vendors, and can
build an updated mapping file.

### Invoice Lifecycle Hub

A **prototype** of an Outlook invoice tracker. It uses fictitious data and doesn't connect to
any real mailbox. It shows a service overview, a feedback page, and a dashboard where invoices
and vendor bills move through approval queues, with the time spent at each stage tracked.

> **Note:** The sign-in on this page is simulated in your browser. It isn't real security or
> access control.

---

## Your data stays local

Company data never belongs in this repository, even a private one. The `.gitignore` already
excludes these locations:

| Location | Contents |
|---|---|
| `apps/recon/data/` | Sample QuickBooks and Infinium exports |
| `apps/sales_tax/data/` | The trial balance cache and sample input files |
| `apps/fifo_inventory/fifo_snapshots/` | Saved FIFO layers and period history |
| `apps/fifo_inventory/app_settings.json` | FIFO tolerance settings |

Before you commit, check `git status` and add specific files rather than everything.

---

## Project layout

```
app.py                  Entry point: page setup, top navigation, shared styling
shared/                 Navigation registry (layout.py), theme, logo
apps/
  dashboard.py          Landing page with one card per tool
  fifo_inventory/       FIFO Inventory
  recon/                Sales Reconciliation (matching/ and workpapers/ packages, tests/)
  sales_tax/            Sales Tax Review
  invoice_hub/          Invoice Lifecycle Hub (static HTML/JS site in site/, tests/)
docs/                   Suite architecture and cross-app rules
.streamlit/config.toml  Forces the light theme
```

Each tool has its own `docs/` folder with detailed notes and a known-issues list.

`automated-outlook-invoice-tracker-phase-1.zip` is the archived source of a separate Node.js
and PostgreSQL version of the invoice tracker. The Streamlit app doesn't use it.

## Testing

Run these from the repository folder:

```powershell
py -m pytest                                  # Sales Reconciliation unit tests
node apps/invoice_hub/tests/test_layers.js    # Invoice Hub; prints "ALL TESTS PASSED"
```

FIFO Inventory and Sales Tax Review don't have unit tests yet.
[docs/suite-architecture.md](docs/suite-architecture.md) explains how to check those tools
headlessly and how to prove that a change didn't alter their results.

## Further reading

| Topic | Document |
|---|---|
| How the suite is wired and how to add a new tool | [docs/suite-architecture.md](docs/suite-architecture.md) |
| FIFO math, layers, and terms | [apps/fifo_inventory/docs/fifo-accounting.md](apps/fifo_inventory/docs/fifo-accounting.md) |
| FIFO upload formats | [apps/fifo_inventory/docs/data-inputs.md](apps/fifo_inventory/docs/data-inputs.md) |
| FIFO preview, close, reopen, and snapshots | [apps/fifo_inventory/docs/period-lifecycle.md](apps/fifo_inventory/docs/period-lifecycle.md) |
| FIFO controls and the Excel report | [apps/fifo_inventory/docs/controls-and-reporting.md](apps/fifo_inventory/docs/controls-and-reporting.md) |
| Sales Reconciliation structure | [apps/recon/docs/architecture.md](apps/recon/docs/architecture.md) |
| Sales Reconciliation matching and duplicate rules | [apps/recon/docs/matching-rules.md](apps/recon/docs/matching-rules.md) |
| Sales Tax Review | [apps/sales_tax/docs/overview.md](apps/sales_tax/docs/overview.md) |
| Invoice Lifecycle Hub | [apps/invoice_hub/docs/overview.md](apps/invoice_hub/docs/overview.md) |
