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


# --- keeping the stylesheets on the design tokens ---------------------------------------------------------------
import re  # noqa: E402

_TOKENS = (STYLES_DIR / "base" / "tokens.css").read_text(encoding="utf-8")
_TOKEN_NAMES = set(re.findall(r"(--pp-[a-z0-9-]+)\s*:", _TOKENS))
# Set at run time by a script or an inline style attribute, so no stylesheet declares them.
_SET_AT_RUN_TIME = {"--pp-sb-right", "--pp-wordmark"}
_GENERIC = {"#ffffff"}  # white means surface, text-on-dark, and more; it stays as written


def _sheets():
    return [(p.relative_to(STYLES_DIR).as_posix(), p.read_text(encoding="utf-8"))
            for p in sorted(STYLES_DIR.rglob("*.css"))]


def test_every_pp_token_a_stylesheet_uses_is_defined():
    undefined = {}
    for name, css in _sheets():
        missing = set(re.findall(r"var\((--pp-[a-z0-9-]+)", css)) - _TOKEN_NAMES - _SET_AT_RUN_TIME
        if missing:
            undefined[name] = sorted(missing)
    assert undefined == {}


def test_no_stylesheet_repeats_a_token_color_as_a_raw_hex():
    """A color that already has a --pp-* token is written as the token, so changing it later is one edit."""
    values = {}
    for m in re.finditer(r"(--pp-[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", _TOKENS):
        values.setdefault(m.group(2).lower(), m.group(1))
    repeats = []
    for name, css in _sheets():
        if name == "base/tokens.css":
            continue
        for number, line in enumerate(css.splitlines(), 1):
            if re.match(r"\s*--[a-z0-9-]+\s*:", line) or "data:" in line:  # a page's own palette, and data URIs
                continue
            for hit in re.findall(r"(?<![-\w&%])#[0-9a-fA-F]{6}\b", line):
                if hit.lower() in values and hit.lower() not in _GENERIC:
                    repeats.append(f"{name}:{number} {hit} -> {values[hit.lower()]}")
    assert repeats == []


def test_stylesheets_have_no_non_breaking_spaces_or_tabs_in_rules():
    bad = [name for name, css in _sheets() if any(c in css for c in "   ")]
    assert bad == []
