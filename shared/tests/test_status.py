"""The status palette lives twice (CSS tokens and Styler hex); these tests keep the copies identical."""
import re
from pathlib import Path

import pandas as pd

from shared.status import PALETTE, status_badge, status_kind, status_styler

THEME = (Path(__file__).resolve().parents[1] / "styles" / "base" / "tokens.css").read_text(encoding="utf-8")


def test_python_palette_matches_theme_tokens():
    for kind, (bg, text) in PALETTE.items():
        assert re.search(rf"--pp-status-{kind}-bg:\s*{bg};", THEME)
        assert re.search(rf"--pp-status-{kind}-text:\s*{text};", THEME)


def test_status_kind_mapping():
    expected = {
        "PASS": "pass", "PASS - No Activity": "pass", "MATCHED": "matched", "REVIEW": "review",
        "REVIEW REQUIRED": "review", "REVIEW_HOLD": "review", "WARNING": "review",
        "TRUE_UNMATCHED": "unresolved", "Unresolved": "unresolved", "FAIL": "fail",
        "EXACT_QBO_DUPLICATE_EXCLUDED": "neutral", "Net": "neutral",
    }
    for label, kind in expected.items():
        assert status_kind(label) == kind, label


def test_badge_escapes_label():
    assert "&lt;b&gt;" in status_badge("<b>")


def test_styler_colors_status_cells_only_and_keeps_values():
    df = pd.DataFrame({"Status": ["PASS", "FAIL", "n/a"], "Amount": [1234.5, 2.0, 0.0]})
    styler = status_styler(df, ["Status"], formats={"Amount": "{:,.2f}"})
    html_out = styler.to_html()
    assert "#eefaf1" in html_out and "#fdecea" in html_out
    assert "1,234.50" in html_out
    pd.testing.assert_frame_equal(styler.data, df)  # the data itself is untouched
    assert styler.export() is not None
    assert styler._compute().ctx[(2, 0)] == []  # "n/a" is unstyled
