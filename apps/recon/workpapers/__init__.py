"""Excel workpaper construction.

Builds the downloadable workbooks (the primary accounting workpaper and the optional
legacy-format export) sheet by sheet. Every sheet builder composes the styling
primitives in ``excel_styles.py`` and pulls its data from a ``ReconciliationResult``.
Nothing here knows about Streamlit -- ``ui_components.py`` is the only caller that
talks to both this package and the browser.

The package re-exports the names callers use, so they keep importing from
``workpapers`` directly. Submodules, in dependency order:

    tables          sheet names, Excel table and structured-reference helpers
    finishing       row autofit and heights, ignored-errors XML, run metadata, saving bytes
    raw_data        the Raw Data sheet
    detail          the Reconciliation Detail sheet, paired display frames, row links
    sheet_parts     reviewer-facing wording, the KPI band, and the color/reason-code legends
    unresolved      the Unresolved Exceptions sheet
    summary_sheets  the Posting Summary and Aggregates sheets
    legacy          the legacy-format workbook
    primary         ``build_primary_workbook``: assembles the primary workpaper
"""

from __future__ import annotations

from .finishing import (
    _autofit_workbook_rows,
)
from .detail import (
    _control_strip_text,
    _match_ref_link_formula,
    paired_display_frames,
    qb_record_outcomes,
)
from .legacy import (
    _legacy_matched_label,
    build_legacy_workbook,
)
from .primary import (
    build_primary_workbook,
)

__all__ = [
]
