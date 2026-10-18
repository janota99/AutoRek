# Sales Reconciliation: known issues

Confirm with the user before changing behavior. Some of these may be intentional.

1. **A few functions are still very long:** `build_unresolved_sheet` (~790 lines),
   `build_reconciliation` (~670), `build_paired_rows` (~510), `validate_reconciliation` (~455).
   The September 2026 cleanup split them into their own modules, unchanged. Breaking them up
   internally is a riskier refactor; lean on the 418 tests if you do.
2. `ui_components.py` has a diagnostics block that prints where `openpyxl`, `workpapers`, and
   `excel_styles` were loaded from. It's useful when a stale install shadows the package.

3. **Digit-less references are "specific" but not "unique" (decided 2026.10).** A PO such as `HOPPER` or `LOWES`
   is still valid evidence and matches on PO + exact amount when it identifies one record on each side. Repeated
   rows with the same reference and amount are no longer paired in source order: they are held for review unless a
   defined field (an invoice number) separates them. Only a short placeholder list (`evidence.WEAK_REFERENCE_WORDS`),
   all-zero values, and 4+ repeated characters are weak. **Still open:** no cross-system native transaction ID is
   mapped, so there is no ID-based tie-breaker beyond the invoice number; dates are deliberately not a matching rule.
4. **Punctuation (decided 2026.10).** Matching compares the identifiers with punctuation kept first (after the documented prefix normalization: case, spacing, a leading `PO`, a trailing `.0`) (`HP-2231` pairs with
   `HP-2231`, `HP2231` with `HP2231`, in either file order). Punctuation is ignored only as a fallback, for the single
   candidate left on each side, and that match is labelled `ACCEPTED_MATCH_WITH_IDENTIFIER_DISCREPANCY`. If several
   candidates remain the rows are held. The comparison key itself still collapses punctuation (`1-23` = `123`), so the
   `Identifier Discrepancy` column is still the place to look. `MATCHING_RULE_VERSION` is `2026.10-EXACT-FIRST-NO-ORDER-PAIRING`.
5. **Customer and date corroborate; they do not decide.** QuickBooks names customers where Infinium numbers
   them, so a customer check is mostly "not comparable" on real data.
6. **Live Excel formulas are unverified in Excel here.** The Posting Summary live checks (including a
   `SUMPRODUCT` over the `ReviewHolds` table) are written as structured references and tested structurally only;
   open a generated workbook in Excel once before relying on them.
7. **Same-amount, no-shared-reference rows are not matched.** On the sample files, 40 of 54 unmatched
   QuickBooks rows have an unconsumed Infinium row at the same amount (repeating price-list totals such as
   3,505.70) with no shared PO or invoice. That is deliberate: equal amounts are not evidence.
