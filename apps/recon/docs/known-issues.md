# Sales Reconciliation: known issues

Confirm with the user before changing behavior. Some of these may be intentional.

1. **`SPRING HOUSE` without a size isn't a recognized product variant.**
   - `PRODUCT_LEXICON` (`matching/core.py`) used to list "Spring House 24 Case" twice, and Python
     kept only the second entry. The dead first entry, which also listed bare `"SPRING HOUSE"`,
     was removed on 2026-09-30, so behavior is unchanged.
   - If bare "SPRING HOUSE" should map to the 24 Case, add it to that entry's variants. That
     changes the Product Aggregate Summary for such rows, so bump `MATCHING_RULE_VERSION`.
2. **A few functions are still very long:** `build_unresolved_sheet` (~785 lines),
   `build_reconciliation` (~655), `build_paired_rows` (~510), `validate_reconciliation` (~455).
   The September 2026 cleanup split them into their own modules, unchanged. Breaking them up
   internally is a riskier refactor; lean on the 261 tests if you do.
3. `ui_components.py` has a diagnostics block that prints where `openpyxl`, `workpapers`, and
   `excel_styles` were loaded from. It's useful when a stale install shadows the package.
