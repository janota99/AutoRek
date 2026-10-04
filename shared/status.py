"""One status vocabulary for the whole suite: PASS / REVIEW / FAIL / matched / unresolved.

The colors live in shared/styles/base (`--pp-status-*` in tokens.css, `.pp-status` in status.css); this module only picks the
class. Every badge carries an icon and its text, so status is never conveyed by color alone.
Display only: nothing here decides a status, it just renders the one the engine already chose.
"""
import html
from typing import Iterable, Optional

import pandas as pd

# Longest prefix wins, so "PASS - No Activity" and "REVIEW REQUIRED" resolve without special cases.
# Underscores read as spaces, so engine codes such as REVIEW_HOLD and TRUE_UNMATCHED resolve too.
_KINDS = (
    ("TRUE UNMATCHED", "unresolved"),
    ("UNRESOLVED", "unresolved"),
    ("WARNING", "review"),
    ("UNMATCHED", "unresolved"),
    ("MATCHED", "matched"),
    ("REVIEW", "review"),
    ("FAIL", "fail"),
    ("PASS", "pass"),
)


def status_kind(label: str) -> str:
    """Return the palette name for a status label, or "neutral" when it isn't one of ours."""
    text = str(label).strip().upper().replace("_", " ")
    for prefix, kind in _KINDS:
        if text.startswith(prefix):
            return kind
    return "neutral"


# Canvas tables (st.dataframe) can't read CSS variables, so the Styler needs literal colors. These are the
# values of --pp-status-*-bg / -text in base/tokens.css; shared/tests/test_status.py fails if the two drift.
PALETTE = {
    "pass": ("#eefaf1", "#17552b"),
    "review": ("#fff8e6", "#5c4300"),
    "fail": ("#fdecea", "#8a1f17"),
}
_PALETTE_KIND = {"matched": "pass", "unresolved": "review"}


def status_styler(df: pd.DataFrame, columns: Iterable[str], formats: Optional[dict] = None):
    """A Styler that colors the status cells of `columns` with the shared palette (display only).

    Cells that are not a known status are left unstyled. A Styler replaces Streamlit's own number
    formatting, so pass `formats={"Amount": "{:,.2f}"}` for any numeric column that must keep a fixed look.
    """
    def cell(value: object) -> str:
        kind = status_kind(value)
        colors = PALETTE.get(_PALETTE_KIND.get(kind, kind))
        return f"background-color: {colors[0]}; color: {colors[1]}; font-weight: 700" if colors else ""

    return df.style.format(formats, na_rep="").map(cell, subset=list(columns))


def status_badge(label: str) -> str:
    """HTML for a pill badge showing `label` in its status color (escaped; safe for unsafe_allow_html)."""
    return f'<span class="pp-status pp-status--{status_kind(label)}">{html.escape(str(label))}</span>'


def status_tile(label: str, count: int) -> str:
    """HTML for a count tile (a status name over a large number), replacing a plain st.metric."""
    return (
        f'<div class="pp-status-tile pp-status-tile--{status_kind(label)}">'
        f'<div class="pp-status-tile-label">{html.escape(str(label))}</div>'
        f'<div class="pp-status-tile-count">{int(count):,}</div></div>'
    )
