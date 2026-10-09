# Sales Reconciliation: known issues

Confirm with the user before changing behavior. Some of these may be intentional.

1. **A few functions are still very long:** `build_unresolved_sheet` (~790 lines),
   `build_reconciliation` (~670), `build_paired_rows` (~510), `validate_reconciliation` (~455).
   The September 2026 cleanup split them into their own modules, unchanged. Breaking them up
   internally is a riskier refactor; lean on the 418 tests if you do.
2. `ui_components.py` has a diagnostics block that prints where `openpyxl`, `workpapers`, and
   `excel_styles` were loaded from. It's useful when a stale install shadows the package.

3. **Decision needed: digit-less references are not "weak".** A PO such as `HOPPER` or `LOWES` still matches
   on exact PO + exact amount. Treating names as weak is the right instinct for generic words, but the existing
   fixtures and sample data use such references, so changing it would move accepted matches. Only a short
   placeholder list (`evidence.WEAK_REFERENCE_WORDS`), all-zero values, and 4+ repeated characters are weak today.
4. **Punctuation is still collapsed in the comparison key** (`AB-12` = `AB12`, `1-23` = `123`). That is the
   existing documented rule; each match whose two sides differ only by punctuation records it, but two distinct
   identifiers that differ only by punctuation can still collide. Review the `Identifier Discrepancy` column.
5. **Customer and date corroborate; they do not decide.** QuickBooks names customers where Infinium numbers
   them, so a customer check is mostly "not comparable" on real data.
6. **Live Excel formulas are unverified in Excel here.** The Posting Summary live checks (including a
   `SUMPRODUCT` over the `ReviewHolds` table) are written as structured references and tested structurally only;
   open a generated workbook in Excel once before relying on them.
7. **Same-amount, no-shared-reference rows are not matched.** On the sample files, 40 of 54 unmatched
   QuickBooks rows have an unconsumed Infinium row at the same amount (repeating price-list totals such as
   3,505.70) with no shared PO or invoice. That is deliberate: equal amounts are not evidence.
