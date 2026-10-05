# Sales Reconciliation: matching and duplicate rules

Read this before changing anything in `matching/`, `duplicates.py`, `fuzzy_po_matching.py`, or
`vendor_aliases.py`. The module docstrings hold the full reasoning; this is the summary.

## Matching order (`matching/engine.py`)

1. Unique PO + invoice + exact signed amount.
2. Unique PO + exact signed amount.
3. Unique invoice + exact signed amount.
4. Same-key duplicate clusters (`_duplicate_cluster_pairs`). When two or more rows share the
   exact same PO/invoice/signed-amount key and carry nothing else that tells them apart, they're
   paired in source-file order. Any leftover row stays unresolved.
5. Unique grouped aggregate by PO and/or invoice. This pass is bounded by `MAX_GROUP_POOL_ROWS`
   (20) and `MAX_GROUP_SIZE` (8); larger pools stay unresolved for review.
6. Confirmed vendor aliases (`vendor_aliases.py`). These post like an exact match, not as a
   review hold, and run even when fuzzy matching is switched off for historical rows.
7. Bounded fuzzy passes (`fuzzy_po_matching.py`) over rows that survive every pass above.

**Amounts must agree exactly to the signed cent.** Anything ambiguous stays unresolved for
review. Never add a similarity-percentage or "closest match" rule.

## Evidence layer (`matching/evidence.py`)

Matching and exception classification read the same evidence through one module.

- **Normalization** (`core.identifier_text`, `clean_po`, `clean_alphanumeric`): numeric cells become
  exact digit strings, never floats; whitespace, zero-width characters, an Excel text prefix (`'0105`) and
  `="..."` wrappers are removed; a trailing `.0` is dropped. Leading zeros are kept. Case, spacing,
  punctuation and a leading `PO`/`P.O.` are ignored when comparing (the documented equivalences). Scientific
  notation (`1.2E+11`) is flagged, never repaired. The source text is kept in `SRC_PO` / `SRC_INV`, and a match
  whose two sides agree only after ignoring punctuation records that on the match.
- **Fallback evidence columns** (mapping keys `po_fallback`, `invoice_fallback`; sidebar multiselects): used
  only where the dedicated column is blank, and only if the cell holds exactly one identifier-like token (4+
  characters including a digit). Several candidates in a description supply nothing.
- **Reference strength:** a placeholder from `WEAK_REFERENCE_WORDS`, all zeros, or one character repeated four
  or more times is **weak**. Weak values are blank in the match keys (`KEY_PO` / `KEY_INV`), so they can never
  create an accepted match. Digit-less names are *not* weak (see known issues).
- **Weak evidence policy:** a weak reference whose Infinium record agrees to the cent is a **posting hold**
  (`REVIEW_HOLD_WEAK_REFERENCE`: the sale may already be recorded). A weak reference with no exact-amount
  candidate is an **informational flag** only (`EVIDENCE_INSUFFICIENT_WEAK_REFERENCE`); the row stays JE support.
- **Link vetting.** Every proposed match (pairs, clusters, groups, alias, typo) is checked before it is
  accepted. For each identifier that was *not* the key, if both sides carry different values:
  - neither value is carried by another record: **non-competing discrepancy**, the match stands and records it
    (`ACCEPTED_MATCH_WITH_IDENTIFIER_DISCREPANCY`);
  - one value is carried by a different record on the other side: **conflict**, the match is refused, nothing is
    consumed, and the row is held (`REVIEW_HOLD_CONFLICTING_LINKS`). The test reads the whole frames, so the
    verdict never depends on pass order.
- **Corroboration** (customer, date) explains a match or hold; it never accepts or rejects. QuickBooks names
  customers and Infinium numbers them, so an unlike customer is reported "not comparable", not a contradiction;
  a date gap is an accounting-period difference, shown apart from identity evidence.
- **Evidence findings** (candidate table, one per unmatched row): `NO_EVIDENCE`,
  `EVIDENCE_INSUFFICIENT_WEAK_REFERENCE[_EXACT_AMOUNT]`, `EVIDENCE_FOUND_AMOUNT_DIFFERS`,
  `EVIDENCE_ALREADY_CONSUMED`, `EVIDENCE_MULTIPLE_CANDIDATES`, `EVIDENCE_EXACT_AMOUNT_NOT_UNIQUE`,
  `EVIDENCE_CONFLICTING_LINKS`, `EVIDENCE_TYPO_CANDIDATES`, `EVIDENCE_INVALID_AMOUNT`. A row is never "no matching
  records" when evidence existed but was rejected. Review items list each candidate ID, the fields that linked it,
  its amount and difference, its date, and whether it was already consumed.

## Classification codes (`QB Disposition Ledger`)

`Classification Code` is one stable value per row, beside the finer `Reason Code`: `ACCEPTED_MATCH`,
`ACCEPTED_MATCH_WITH_IDENTIFIER_DISCREPANCY`, `ACCEPTED_HISTORICAL_MATCH`, `TRUE_UNMATCHED`, `AMOUNT_VARIANCE`,
`MULTIPLE_CANDIDATES`, `CONFLICTING_IDENTIFIER_LINKS`, `ALREADY_REPRESENTED_OR_CONSUMED_EVIDENCE`,
`POSSIBLE_HISTORICAL_EVIDENCE`, `POSSIBLE_DUPLICATE`, `CONFIRMED_DUPLICATE`, `WEAK_EVIDENCE_REVIEW`,
`INVALID_AMOUNT`, `PO_REUSE`. Review holds stay separate from true unmatched JE support.

## Fuzzy and alias matching

- **Whole-word containment:** every significant word of the shorter PO reference must appear in
  the longer one. These matches are always held for review.
- **Controlled typo:** exactly one word may differ by one inserted, deleted, or substituted
  character, with an exact amount and exactly one candidate on each side. These post as a
  separately labeled match (`Controlled PO Typo + Exact Amount`), not a review hold.
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

Every accepted historical match records the historical file name, the source row ID, its period and date, the
method, and any identifier discrepancy (`Secondary Source File`, `Secondary Row ID`, `Secondary Period`,
`Secondary Date`, `Identifier Discrepancy`). The same normalization and link vetting apply. A period gap is shown,
never used as evidence.

## Reviewer decisions, approval, and export controls

- `matching/review_decisions.py` layers decisions on an immutable engine result. `apply_review_decisions` returns a
  copy carrying `review_adjustments` and `adjustment_bridge`; the engine's ledger is never edited.
  Actions: `CONFIRM_MATCH` (names the Infinium rows; each must be an unconsumed primary row, used by one decision
  only; a different amount is recorded, never posted), `RELEASE_TO_JE` and `CARRY_FORWARD` (holds only),
  `EXCLUDE` (JE support or a hold; needs a supporting reference). Reviewer, date, and reason are required.
- The result is **calculated** ("Reviewer-adjusted JE") until `record_approval` stores an approver, date, and the
  amount approved. Approval is refused while any hold is undecided or carried forward. The workbook says
  "FINAL APPROVED JE" only for a recorded approval.
- Decisions are applied in the Downloads tab and the workbook regenerated; they open pre-filled in the
  Reviewer Disposition columns. Release and Exclude totals on the Posting Summary are live formulas, so edits in
  Excel recalculate them, and two live checks compare the live total with the exported one and count decisions
  missing a reviewer, date, or comment. **Excel never re-checks record consumption or exact-cent agreement.**
- `matching/export_checks.py` runs identity checks (rows), then counts, then dollars, from the result's own row
  sets: one disposition per row; populations match the ledger; no record consumed twice (individual, grouped,
  historical); every match and clearance ties to the cent; detail/exception/hold identities; the proposed JE
  equals its exception rows; the adjustment bridge reconciles. `build_reconciliation` and
  `build_primary_workbook` refuse (ValueError) on any failure. These are *static* (validated at export); the
  Audit & Controls sheet labels them so and lists the live ones.

## Product classification (Aggregates sheet only)

`product_match` (`matching/core.py`) labels each QuickBooks description for the Aggregates
sheet. It never feeds a financial match, but it does change product grouping, so a change to it
bumps `MATCHING_RULE_VERSION`.

- QuickBooks `QTY` is a **case** count. Bottles per case comes from the standard name in
  `PRODUCT_LEXICON` ("Lowes 32 Case" means 32). Only 24, 32 and 40 exist, and a test fails if a
  name lacks one. There is no fallback size.
- **Packaging words mean nothing.** CASE, CASES, CS, CSE, PACK, PACKS, PK, PKS, PCK, PKG, COUNT
  and CT are stripped before the lookup, as whole words or stuck to a number (`40PK`), never
  inside another word (`PACKAGING`). So `PP40`, `PP 40 CASE` and `PP40PK` are the same product,
  and text that is only these words is **Unrecognized product**.
- As a last resort, when nothing else matched, CASE(S), PACK(S) or COUNT glued to the end of a
  word comes off if what's left is a word from a lexicon name or another packaging word
  (`ALLSUPSPACK`, `PP 40 CASEPACK`). `DISCOUNT` and `ACCOUNT` are left alone, so those lines
  stay **Unrecognized product**.
- The lexicon lookup (exact, else `get_close_matches` at 0.82) finds the nearest product name.
  It tries the stripped text first, then the text as written, against every lexicon name both
  as written and stripped, so stripping only ever adds a match (`PP 24 CASE PAK` still lands).
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
- Identifier normalization, reference strength, link vetting, and the evidence findings are part of the matching
  rules: a change to any of them bumps `MATCHING_RULE_VERSION`.
- Any change here must keep `py -m pytest -W error::FutureWarning` green.
