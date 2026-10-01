# Sales Reconciliation: known issues

Confirm with the user before changing behavior. Some of these may be intentional.

1. ~~**`SPRING HOUSE` without a size isn't a recognized product variant.**~~ **Resolved
   2026-09-30.** The size-aware product classifier gives a one-size brand its only pack size when
   the text has none, so bare "SPRING HOUSE" now counts as Spring House 24 Case
   (`MATCHING_RULE_VERSION` `2026.09-SIZE-AWARE-PRODUCTS`). See
   [matching-rules.md](matching-rules.md#product-classification-aggregates-sheet-only).
2. **A few functions are still very long:** `build_unresolved_sheet` (~785 lines),
   `build_reconciliation` (~655), `build_paired_rows` (~510), `validate_reconciliation` (~455).
   The September 2026 cleanup split them into their own modules, unchanged. Breaking them up
   internally is a riskier refactor; lean on the 309 tests if you do.
3. `ui_components.py` has a diagnostics block that prints where `openpyxl`, `workpapers`, and
   `excel_styles` were loaded from. It's useful when a stale install shadows the package.
