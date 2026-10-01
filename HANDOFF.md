# Handoff: Recon bottle count, grouped exceptions summary, workpaper speed-up

**Status (2026-09-30):** A `/grill-me` session settled the design for three Recon features and two
bug fixes. **No code has been written yet**, and the working tree is clean on `main`. The user
was offered "build in the order below, one feature per commit" and hasn't confirmed it yet, so
ask before you start.

Read first: `CLAUDE.md` (rules), `apps/recon/docs/architecture.md` (module map),
`apps/recon/docs/matching-rules.md`, `apps/recon/docs/known-issues.md`.

The user is an accountant, not a developer. Explain choices in plain language and ask before
anything that changes a number.

---

## 1. Bottle count on the Aggregates sheets

The Aggregates sheets are built by `build_aggregates_sheet` / `_write_aggregate_table` in
`apps/recon/workpapers/summary_sheets.py`. That code produces both the primary "Aggregates" tab
and the Legacy "Product Aggregate Summary" tab. The data comes from `build_product_summary` and
`build_customer_summary` in `apps/recon/matching/summaries.py`.

QuickBooks `QTY` is a **case** count (sample: 1,848 "ALLSUPS 24 CASE" = $3,836.58).

**Decisions:**
- **Pack size** is parsed from the *standard* product name in `PRODUCT_LEXICON`
  (`matching/core.py`): "… 24 Case" means 24. Only 24, 32 and 40 exist. Add a test that fails if
  any lexicon name lacks an explicit 24, 32 or 40. Don't use a blanket fallback to 24.
- **New lexicon entries:**
  - `Lowes 32 Case`: `LOW 32`, `LOW32`, `LOWES 32`, `LOWES 32 CASE`.
  - `Panhandle Pure 32 Case`: `PP32`, `PP 32`, `PPL32`, `PPL 32`, `PP 32 CASE`, `PANHANDLE PURE 32 CASE`.
  - `Food Club 32 Case`: `FC32`, `FC 32`, `FC32 CASE`, `FOOD CLUB 32`, `FOOD CLUB 32 CASE`,
    `FOODCLUB 32`, `FOODCLUB32 CASE`. The user named only `FC32`; the others follow the existing
    Food Club 24/40 variants.
- **Size-aware classifier.** Today `_cached_fuzzy_match` (`get_close_matches`, cutoff 0.82)
  ignores size. Verified mis-classifications:
  - `LOWES 32 CASE` and `LOWES` give Lowes 24.
  - `PPL 12 CASE` gives PP 24.
  - `ALLSUPS 40 CASE` gives Allsups 24.
  - `FOOD KING` and `KINGS` give Food King 40.

  New rules:
  - **24-only brands** (Allsups, Juniors, Plains, Toot N Totum, Spring House) default to 24 when
    the text has no size.
  - **Size in the text disagrees with the product**, or **no size on a multi-size brand** (Lowes,
    Panhandle Pure, Food Club, Food King) → **"Needs review – size unclear"**. Remove the bare
    `LOWES` and `FOOD CLUB` variants from the 24 entries.
  - **Any other text containing "32"** (not Lowes 32, PP 32 or Food Club 32) → **"New item – 32-count"**.
  - **No match at all** → **"Unrecognized product"**. Today those rows are silently dropped
    (`work = qb[qb[PRODUCT_STANDARD].notna()]`).
- **Product table columns:**
  `Product Name | Bottles per Case | Case Quantity | Bottle Count | Product Value`.
  - Bottle Count is a **live Excel formula** (`=Bottles per Case × Case Quantity`).
  - Review rows sit at the bottom with Bottles per Case and Bottle Count blank.
  - The Case Quantity and Value totals must agree with **all** primary QuickBooks lines in the
    period.
- **"Items needing review" list** below the product table: original QuickBooks text, invoice,
  cases and amount for each flagged line. Show it only when non-empty; otherwise one line,
  "No items need review." The user expects this to be very rare.
- **Customer table columns:** `Customer Name | Case Quantity | Bottle Count | Customer Value`.
  - Bottle Count is a **stored value**, summed line by line, because customers mix pack sizes.
  - The caption names any cases with no bottle count.
- **Rename** "Product Quantity" to "Case Quantity" and "Customer Quantity" to "Case Quantity".
  Three existing tests assert the old headers: `apps/recon/tests/test_workpapers.py` around lines
  1818–1893. Update them on purpose.
- **Matching is unaffected:** `PRODUCT_STANDARD` is only read by `build_product_summary`
  (verified with grep). Product grouping does change, so **bump `MATCHING_RULE_VERSION`** (see
  `apps/recon/docs/matching-rules.md`).
- **Docs:** update `matching-rules.md`. In `known-issues.md`, item #1 (bare "SPRING HOUSE") is
  resolved by the 24-only default, so mark it resolved.
- **Sample data:** every description in `apps/recon/data/QBO Data NEW.xlsx` is unambiguous
  (259 detail rows), so the product rows there shouldn't move. Use that as a check.

## 2. Grouped summary on Unresolved Exceptions

The sheet is built by `build_unresolved_sheet` in `apps/recon/workpapers/unresolved.py`.

- **What to group:** row 2 down to the row just above the exceptions-table `header_row`
  (`fiscal_total_row + 3`). Compute the end on each run, because the reason-code glossary and the
  fiscal-period table change length. "Rows 2–26" was just the user's sample. Row 1 (the title)
  stays visible.
- **Default state:** collapsed on open (`hidden=True` on the grouped rows). Put the +/– button at
  the top with `ws.sheet_properties.outlinePr.summaryBelow = False`.
- **Freeze panes:** keep them at `A{data_row}`. When collapsed, the frozen area shrinks to the
  title row plus the header row.
- **Make the summary obvious:** add a marker to the row-1 title band, e.g.
  "⊞ GROUPED SUMMARY — click + / – beside row 1 to show or hide KPIs, reason codes, and exceptions
  by fiscal period". The user insisted the summary must be obvious when collapsed.
- **Check the row-height code:** `finishing._autofit_workbook_rows` and `fix_row_height` set
  explicit heights. Make sure they don't break the hidden rows.
- **Posting Summary:** reword its nav note ("reason code definitions are in that sheet's own frozen
  header", `summary_sheets.py`) to mention the grouped summary.

## 3. Workpaper build speed (the "Prepare Sales Reconciliation" button)

**What was measured on the sample, without the profiler:**
- `build_primary_workbook` takes about 0.85 s.
- Matching takes about 0.3–0.5 s.
- The Legacy workbook takes about 0.6 s.

Most of the cost is openpyxl hashing a style object on every cell assignment (in
`excel_styles._format_body_block` and similar helpers).

A throwaway prototype brought the build to **0.48 s (about 45% faster)**. It sets the styles on
the first cell of each distinct look, keyed on `(even/odd fill, number_format)`, then copies
`cell._style` to the other cells.

**Decisions (option C):**
- Apply the style-reuse approach across the formatting helpers in `apps/recon/excel_styles.py`.
  The Legacy workbook benefits too.
- Pin `openpyxl` to `3.1.x` in `requirements.txt` (currently `>=3.1.5`). Add a test that would
  catch a future openpyxl change breaking the `_style` copy.
- **Prove the output is unchanged:** build the workbook before and after on `apps/recon/data/`
  and compare every cell's value, formula, number_format, font, fill, border and alignment, plus
  row heights, merges, freeze panes, tables and data validations. Keep the comparison script in
  your scratchpad. Never commit the data.
- **Timing line in the Downloads tab** (`ui_components.py`, near `prepare_primary_`), e.g.
  "Workpaper built in X s · page refreshed in Y s". The user says the Prepare click is "definitely"
  the slow step but gave no row count or seconds. The build itself is under 1 s on the sample, so
  the Streamlit page rerun may be most of the wait. The readout will show which.

## 4. Bug fixes (approved)

1. **Stale Legacy download.** After a re-run, `legacy_workbook` stays in session state, so
   Download Legacy serves the previous run's file under the new run ID. Pop `legacy_workbook`
   wherever `primary_workbook` is popped: `apps/recon/app.py` lines ~224 and ~681, and
   `apps/recon/utils.py` line ~79.
2. **Out-of-date caption.** The Posting Summary row-2 caption (`summary_sheets.py`, in
   `build_posting_summary_sheet`) still refers to "the Analytics workbook's Executive Summary".
   Point it at the existing sheets.

Neither fix changes an accounting number. No known-issues entry is needed once they're fixed.

---

## Verification

- `py -m pytest`: 261 tests now, and they must stay free of FutureWarnings. Add tests for:
  - the pack-size parse and the lexicon guard;
  - each size-aware classifier outcome from the table above;
  - the 32 rule;
  - the review rows and the totals agreeing with QuickBooks;
  - the customer table's line-by-line bottles;
  - the group's range, hidden state and `summaryBelow`;
  - the Legacy workbook being cleared on re-run.
- A headless pipeline with the real sample files works outside Streamlit. Call
  `ingestion.list_source_sheets`, `detect_header_row`, `read_source_file`, `infer_column` with
  `QB_COLUMN_PATTERNS`/`INF_COLUMN_PATTERNS`, `filter_qb_subtotal_rows`, then
  `build_reconciliation(..., vendor_aliases=load_vendor_aliases())` and `build_primary_workbook`.
  AppTest can't drive `file_uploader` (see `docs/suite-architecture.md`).

## Suggested skills

- `/run`: launch the app and check the Downloads tab timing line and the Aggregates/Unresolved
  sheets in a real session.
- `anthropic-skills:xlsx`: inspect the generated workbooks (grouping, formulas, column layout).
- `/code-review`: before committing each feature.
- `/simplify`: after the styling refactor, to keep `excel_styles.py` tidy.
- `/grill-me`: only if a new design question comes up (e.g. the user reports real-month timings
  and wants a second speed-up round).
