"""Every stylesheet lives under shared/styles/ and is read only by shared/styles/__init__.py."""
from pathlib import Path

import pytest

from shared import styles

ROOT = Path(__file__).resolve().parents[2]
STYLES_DIR = ROOT / "shared" / "styles"


def test_every_listed_stylesheet_exists_and_is_nonempty():
    for name in styles.BASE_FILES:
        assert (STYLES_DIR / "base" / f"{name}.css").read_text(encoding="utf-8").strip(), name
    for name in styles.PAGES:
        assert styles.page_css(name).strip(), name


def test_no_unlisted_stylesheet_files():
    on_disk = {p.relative_to(STYLES_DIR).as_posix() for p in STYLES_DIR.rglob("*.css")}
    listed = {f"base/{n}.css" for n in styles.BASE_FILES} | {f"pages/{n}.css" for n in styles.PAGES}
    assert on_disk == listed


def test_base_loads_tokens_before_the_rules_that_use_them():
    css = styles.base_css()
    assert css.index("--pp-navy:") < css.index("var(--pp-navy)")
    assert css.index("--pp-status-pass-bg:") < css.index(".pp-status-tile")


def test_unknown_page_is_rejected():
    with pytest.raises(ValueError):
        styles.page_css("nope")


def test_css_is_only_stray_outside_styles_dir_in_the_invoice_hub():
    strays = [p for p in (ROOT / "apps").rglob("*.css") if "invoice_hub" not in p.parts]
    strays += list((ROOT / "shared").glob("*.css"))
    assert strays == []


def test_dashboard_and_sales_page_render_with_their_stylesheets():
    from streamlit.testing.v1 import AppTest

    for view in (None, "pricing"):
        at = AppTest.from_file(str(ROOT / "apps" / "dashboard.py"), default_timeout=60)
        if view:
            at.session_state["pp_view"] = view
        at.run()
        assert not at.exception, (view, at.exception)
