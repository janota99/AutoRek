# Sales Reconciliation: known issues

Confirm with the user before changing behavior. Some of these may be intentional.

1. **A few functions are still very long:** `build_unresolved_sheet` (~790 lines),
   `build_reconciliation` (~670), `build_paired_rows` (~510), `validate_reconciliation` (~455).
   The September 2026 cleanup split them into their own modules, unchanged. Breaking them up
   internally is a riskier refactor; lean on the 355 tests if you do.
2. `ui_components.py` has a diagnostics block that prints where `openpyxl`, `workpapers`, and
   `excel_styles` were loaded from. It's useful when a stale install shadows the package.
