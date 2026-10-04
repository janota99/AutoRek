"""The suite's stylesheets, in one place, and the one way they reach the browser.

    shared/styles/base/    loaded on every page (apply_template), in this order:
        tokens.css           design tokens (--pp-*): colors, shadows, the status palette
        chrome.css           suite chrome: account strip, sidebar logo, captions, "View Plans" pill, top-bar logo
        status.css           PASS / REVIEW / FAIL / matched / unresolved badges and tiles (shared/status.py)
    shared/styles/pages/   one file per page, loaded by that page after the base (so a page can override it):
        dashboard.css, sales-page.css, fifo.css, recon.css, sales-tax.css

The Invoice Hub is the exception: it runs in an iframe that cannot see this page's CSS, so it keeps its own
apps/invoice_hub/site/styles.css.

Streamlit's `st.markdown` and `st.html` both inline a <style> tag, so a page is styled by `inject_page(name)`
or, inside an `st.html` block, by `style_tag(name)`. Cascade order is the injection order: base first.
"""
from pathlib import Path

import streamlit as st

_DIR = Path(__file__).resolve().parent
BASE_FILES = ("tokens", "chrome", "status")
PAGES = ("dashboard", "sales-page", "fifo", "recon", "sales-tax")

# Non-breaking spaces pasted into a stylesheet silently break the rule they sit in.
_SPACE_FIXES = str.maketrans({" ": " ", " ": " ", " ": " "})


def _read(relative: str) -> str:
    return (_DIR / relative).read_text(encoding="utf-8").translate(_SPACE_FIXES)


def base_css() -> str:
    """The CSS every page gets: tokens, then chrome, then status."""
    return "\n".join(_read(f"base/{name}.css") for name in BASE_FILES)


def page_css(name: str) -> str:
    """The CSS of one page (a name in PAGES)."""
    if name not in PAGES:
        raise ValueError(f"Unknown page stylesheet {name!r}; expected one of {PAGES}")
    return _read(f"pages/{name}.css")


def style_tag(name: str) -> str:
    """A <style> tag for a page stylesheet, for pages drawn with st.html."""
    return f"<style>{page_css(name)}</style>"


def inject_base() -> None:
    try:
        st.markdown(f"<style>{base_css()}</style>", unsafe_allow_html=True)
    except OSError:
        pass  # The suite still works without its stylesheet, just less polished.


def inject_page(name: str) -> None:
    """Inline a page stylesheet; the page still works, just less polished, if it can't be read."""
    try:
        css = page_css(name)
    except OSError as exc:
        st.warning(f"The {name} stylesheet could not be loaded: {exc}")
        return
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
