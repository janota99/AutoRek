# Sales Reconciliation: matching and duplicate rules

Read this before changing anything in `matching/`, `duplicates.py`, `fuzzy_po_matching.py`, or
`vendor_aliases.py`. The module docstrings hold the full reasoning; this is the summary.

## Matching order (`matching/engine.py`)

1. Unique PO + invoice + exact signed amount.
2. Unique PO + exact signed amount.
3. Unique invoice + exact signed amount.
4. Unique grouped aggregate by PO and/or invoice. This pass is bounded by `MAX_GROUP_POOL_ROWS`
   (20) and `MAX_GROUP_SIZE` (8); larger pools stay unresolved for review.
5. Bounded fuzzy passes (`fuzzy_po_matching.py`) over rows that survive every exact pass.

**Amounts must agree exactly to the signed cent.** Anything ambiguous stays unresolved for
review. Never add a similarity-percentage or "closest match" rule.

## Fuzzy and alias matching

- **Whole-word containment:** every significant word of the shorter PO reference must appear in
  the longer one. These matches are always held for review.
- **Controlled typo:** exactly one word may differ by one inserted, deleted, or substituted
  character, with an exact amount and exactly one candidate on each side.
- **Order:** exact-token components resolve before typo-level ones, so a look-alike row can't
  drag a clean match into a larger cluster.
- **Vendor aliases** (`vendor_aliases.json`) record human-confirmed identity pairs that no string
  rule could find (e.g. a surname on one system, a first name on the other). Each entry needs
  `confirmed_by`, a date, and a rationale.

## Duplicates (`duplicates.py`)

A shared PO + invoice + signed-amount key only **identifies** a duplicate relationship. An
excess row is excluded only when it's **confirmed** to be a copy of the same line, by either:

- a line-level source ID, or
- an identical fingerprint over the explicit stable attributes, with enough identity-grade evidence.

Anything else stays visible as a potential duplicate. The `matching` package consumes duplicate
dispositions first, so a duplicate candidate can never be repurposed as an amount error.

## Historical (secondary) rows

Historical rows may only clear exceptions in the **opposite** primary source. Historical
QuickBooks can only clear primary Infinium, and the reverse. Unused historical rows never create
exceptions. Historical rows that overlap their own primary dataset can't clear an exception.

## Product classification (Aggregates sheet only)

`product_match` (`matching/core.py`) labels each QuickBooks description for the Aggregates
sheet. It never feeds a financial match, but it does change product grouping, so a change to it
bumps `MATCHING_RULE_VERSION`.

- QuickBooks `QTY` is a **case** count. Bottles per case comes from the standard name in
  `PRODUCT_LEXICON` ("Lowes 32 Case" means 32). Only 24, 32 and 40 exist, and a test fails if a
  name lacks one. There is no fallback size.
- The lexicon lookup (exact, else `get_close_matches` at 0.82) finds the nearest product name.
  The size written in the text then decides:
  - text says 32 but the product isn't Lowes 32, Panhandle Pure 32 or Food Club 32:
    **New item – 32-count**;
  - no product found (or a blank description): **Unrecognized product**;
  - text gives a size that differs from the product's: **Needs review – size unclear**;
  - text gives no size: a one-size brand (Allsups, Juniors, Plains, Toot N Totum, Spring House)
    takes its 24; a multi-size brand (Lowes, Panhandle Pure, Food Club, Food King) goes to
    **Needs review – size unclear**. That's why bare `LOWES` and `FOOD CLUB` aren't variants.
- Review rows sit at the bottom of the product table with no pack size or bottle count. Every
  QuickBooks line in scope lands in exactly one row, so the Case Quantity and Value totals agree
  with QuickBooks. An "Items needing review" list under the table shows each flagged line.
- Product Bottle Count is a live formula (`=Bottles per Case × Case Quantity`). Customer Bottle
  Count is a stored value summed line by line, because customers buy several pack sizes; the
  caption names any cases left out.

## Versions

- `APP_VERSION` and `MATCHING_RULE_VERSION` (in `matching/core.py`) are part of the run
  signature and are stamped into the workpaper. Bump `MATCHING_RULE_VERSION` when a rule
  change can change match results.
- `duplicates.DUPLICATE_RULE_VERSION` versions the duplicate rules.
- Any change here must keep `py -m pytest -W error::FutureWarning` green.
