# Data inputs: Master Grid and Receipts

> **Current page (input processing 2.5.0, `intake.py`).** The page reads two reports. *Materials are found by name or alias*
> (`materials.py`: case, spaces and punctuation ignored; `ITM-nn` product IDs; a spelling that fits two materials is
> ambiguous and never guessed; the person's saved spellings win). The **ending report** is either one row per material
> (`Item`, `Ending Quantity`, optional period column) or the wide Master Grid, of which only the selected period's column is
> read; the beginning balance is never read from a file, it is the last closed period's saved layers.
> **The ledger is continuous, not a census.** Every material carries its saved layers forward. A material absent from the
> ending report, or with a blank quantity, has *no count*: zero usage, ending = beginning + its receipts, no exception.
> A supplied quantity gives usage = beginning + receipts - quantity, consumed oldest-first; an explicit `0` is a count and
> depletes everything. Receipts are appended only to the materials they name. Nothing is invented: no receipts, no zero
> balances. Only a material counted twice, or with a negative/text quantity, blocks the calculation. **Receipts are assigned to the
> period by delivery date** (`period_window`: the Workspace fiscal calendar if it covers the fiscal year; else the day after
> the last closed period; else everything up to the period end); rows in other periods are not evaluated, only counted.
> A cost of `0` is used and noted; a blank, text or negative cost rejects the row, each rejected row with its reason.
>
> **Preferred input: operations' Raw Goods & Receivings workbook (`workbook_import.py`).** One upload fills both sources.
> - `RECEIVED LOG` (header row; found by header names, with a note if columns moved): material = **ITEMS** (B; DESCRIPTION holds a
>   vendor and never decides it), date = DATE DELIVERED (D), PO # (H), QUANTITY (I), **PRICE (J) = the receipt's total cost**, PERIOD (K),
>   INVOICE # (L). Unit cost is PRICE / QUANTITY. Only rows dated inside the selected period (calendar window) are evaluated; the rest
>   of the history is counted, not examined. A row already inside the saved layers' lineage (same material, date, quantity, total, PO,
>   invoice) is not added again. Column K is a cross-check, never the filter: a label that disagrees with the date warns (the date
>   wins), and a row labelled for this period and fiscal year but dated outside it is listed as not used.
> - **Every valid receipt is recognized exactly once, around an explicit receipt cutoff.** The opening FIFO layers are the
>   authoritative inventory position on the cutoff day; the receipt register tracks everything recognized after it. The cutoff
>   defaults to the day before the first period you process (P11's end for FY2026 P12), can be chosen once on the page, is
>   stored by the first period closed with a register, and never moves (`receipt_cutoff`; the opening layers are that period's
>   saved beginning layers, `cutoff_layers`). Each closed period stores the receipts it recognized, as reported, including those
>   later consumed in full, so "already processed" never depends on the layers left. A log row the register accounts for is
>   not added again; matching is per occurrence, so two genuinely identical deliveries are each recognized once.
>   - **Dated after the cutoff and before the period, not in the register:** *late*, recognized now, informational flag.
>   - **Dated on or before the cutoff:** presumed already inside the opening layers, not imported merely for being unregistered,
>     **unless** its period label (column K, else the merged `PERIOD n FYE ...` heading above it) assigns it to a period after
>     the cutoff and no later than this one (the 7/31 caps receipt labelled P12). Then the opening layers are asked
>     (`opening_layer_evidence`): a layer that day at the same unit cost or quantity = already included (skipped, and recorded);
>     nothing that could be it = recognized now; evidence that cannot tell (a same-day layer at another cost and quantity, a
>     consolidated layer spanning the date, a layer with no readable date, or K and the heading disagreeing) = that one
>     receipt is listed under *Receipts to confirm once* and blocks the calculation until answered "Include in this period" or
>     "Already in the opening layers". The answer is stored at close (an "opening layers" register entry), so it is never asked again.
>   - **Dated after the period:** pending for its own period.
>   Three dates stay distinct: **delivered** (as reported), **reported period** (the label), **recognized in** (the period the books
>   took it). A late receipt older than the newest carried layer joins the queue after the layers already carried (its engine date is
>   clamped); the register keeps the real delivery date.
> - **Naming equivalences** (`materials.match_key`): a *case* is a *pack* and *film* is *rolls*, applied only when comparing names.
>   Every other word and number (customer, size, item number) must still agree, so `PP 24 Rolls` = `PP24 Film` but never
>   `PP 40 Rolls`, `PP 24 Cases`, or a bare `Film`/`Rolls`/`Cases`. No unit-of-measure conversion is implied: quantities and
>   costs keep the meaning reported. Add a spelling to `known_spellings.py` for anything the equivalences do not cover.
> - Natural Caps and CSI Caps are one material (alias 4: the company changed vendors and renamed the product). Both names map
>   to it, quantities and layers form one history, and each receipt keeps its own item name and vendor in the register.
> - `PERIOD END <fiscal year>` (row of `Period 1`...`Period 13` headings, material in Description, C): only the first table is read.
>   Section headings, non-inventory supplies (pallet wrap, slip sheets, parts, paper...) and the case/pack tables below are not
>   exceptions. Two spellings the books map to one material (Natural Caps-SSB and CSI Caps) are added and noted.
> - Sheets: a hidden duplicate beside exactly one visible sheet (`RECEIVED` beside `RECEIVED LOG`, `PERIOD END 20261`) is skipped with a
>   note. No match, or several visible candidates, is a problem the page asks you to resolve; a `PERIOD END` sheet for another year is
>   never used. A duplicated or missing required header is an error naming the columns.
> - **Old rows with unreadable dates** are shown in one concise table (source row, material, vendor, date as written, PO, invoice,
>   quantity, cost, period label) under *Old rows with unreadable dates, not used* on the page and in the workpaper's Checks sheet, so you can
>   confirm none belongs to the period. They are never exceptions.
> - A row with an unreadable date is old junk (a note and a list under Input details) unless its PERIOD label names this period and
>   fiscal year, in which case it is a rejected row to fix. Separate uploads remain as a fallback.
> The sections below describe the older alias/period-column parsers in `ingestion.py`, still used by the templates.

Both uploads accept `.csv`, `.xlsx`, or `.xlsm`. If a workbook has several sheets,
the UI asks which one to use (`_excel_sheet_names` / `_read_uploaded_table` in
`ingestion.py`). Templates that pass validation as-is come from
`upload_templates.py` (the "Need a starting template?" expander). The Master Grid template is prepared for the
selected period: the prior period's quantity and value come from the stored layers, the current period's quantity is
blank for the count, and its value is blank because the app derives it.

## Master Grid (UI label: "Ending Inventory Workbook")

One row per product alias, with a quantity column for each period. Value columns are optional and ignored (the app derives values from its stored FIFO layers).

| Column | Detection |
|---|---|
| Alias | Header `ALIAS` / `PRODUCT ALIAS` (matched ignoring case and punctuation); otherwise the **first column**. Non-numeric alias rows are dropped. |
| Quantity for period *n* | `n`, `0n`, `Pn`, `PDn`, `P.n`, … (1–13), via `parse_period_column_header`. |
| Value for period *n* (optional, ignored) | The same token with a trailing `V`: `12V`, `PD12V`. Never required; a non-numeric value cell is still reported as an invalid cell. |

In app session state the grid is normalized to `PRODUCT ALIAS`, `DESCRIPTION`,
`01`…`13`, `01V`…`13V` (`_blank_master_grid` in `app.py`). An upload is *merged*
into this grid by `apply_updates_to_master_grid`, and only known aliases are applied.

**Errors that block the preview** (collected in `app.py` plus `validate_master_grid`):
- The beginning and ending quantity columns for the selected period are missing (P1's beginning column is `13`).
- A known alias is missing, or an alias appears twice.
- A required quantity cell is blank, non-numeric, or negative.

Unknown aliases only produce a warning.

The "Manual Entry / Paste" expander lets the user edit period cells directly
(alias/description are locked). Any edit changes the run signature, which marks an
existing preview stale.

## Receipts (UI label: "Current Period Receipts")

`prepare_receipts_upload(df, current_period)` upper-cases the headers, then finds each column by candidate names:

| Canonical | Accepted headers | Required |
|---|---|---|
| `QUANTITY` | QUANTITY, QTY, QUANTITY RECEIVED, QTY RECEIVED — else **column 10 by position** | yes |
| `PRICE` (total value) | PRICE, TOTAL COST, TOTAL PRICE, AMOUNT, COST, TOTAL — else **column 11 by position** | yes |
| `PRODUCT ALIAS` | PRODUCT ALIAS, ALIAS, PRODUCT | yes |
| `DATE DELIVERED` | DATE DELIVERED, DELIVERY DATE, DATE RECEIVED, DATE | yes |
| `SOURCE PERIOD` | PERIOD, PD, FISCAL PERIOD | no |
| lineage (optional) | `PO #`, `INVOICE #`, `ITEMS`, `UOM`, `COST BASIS`, `TRANSACTION TYPE` | no |

Numbers may include `$`, thousands commas, and `(parentheses)` for negatives (`_coerce_accounting_series`, `_finite_number`).

**Period filtering:** when there's a period column, `extract_period` pulls 1–13 out
of values like `P12`, `P.12 FYE 2026`, `PD 12`, `12`, and only rows for the
selected period are kept. Rows whose period can't be read are dropped with a
warning. **The fiscal year in the label is not checked.** With no period column,
every row counts as the current period (with a warning).

**Row outcomes:**
- **Rejected (error, blocks close):** unknown alias, qty ≤ 0, value < 0, bad date. Source rows are reported using spreadsheet numbering (header = row 1).
- **Warnings (need acknowledgement to close):** possible duplicates (same alias/date/qty/price/PO/invoice), zero-value receipts, the positional column fallback, rows with an unreadable period.

## Accounting-number parsing

Always use `ingestion._finite_number(value, field_name)` for a single value. It
strips `$` and `,`, turns `(x)` into `-x`, rejects NaN/inf, and raises a
`ValueError` that names the field. Use `_excel_safe_text` for any user text
written back to Excel: it prefixes `= + - @` with `'` to block formula injection.
