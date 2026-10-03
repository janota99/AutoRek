"""Dashboard: the landing page, with one card per application, built from the registry in shared/layout.py.

The page is one hand-written HTML block (styled by dashboard.css, animated by dashboard.js) rather
than Streamlit widgets. Each card is a plain link to the app's page; dashboard.js routes the click
through Streamlit's own navigation so the session is kept.

The signed-in person's invoice dashboard lives on the Invoice Lifecycle Hub page and Reviews &
Feedback is its own page in the navigation bar; the account strip (shared/suite_banner.js) is on every page.
"""
import html
import json
from pathlib import Path

import streamlit as st

from apps import sales_page
from shared.layout import APPS

_HERE = Path(__file__).resolve().parent

_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>'
_DOC = '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>'

# Card icons, keyed by the app's url_path. Streamlit strips inline SVG from HTML, so dashboard.js
# inserts these into each card's .pp-icon tile. An app without an entry gets a generic tile.
_ICONS = {
    # Boxes advance along a track toward the arrow, first in, first out: a new box fades in on the
    # left while the oldest fades out on the right. Animated in dashboard.css.
    "fifo-inventory": (
        '<svg class="pp-fifo" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M2 16h20M19 13l3 3-3 3"/>'
        '<g class="pp-fifo-queue">'
        '<rect class="pp-fifo-in" x="2" y="9" width="4" height="4" rx="1"/>'
        '<rect x="8" y="9" width="4" height="4" rx="1"/>'
        '<rect x="14" y="9" width="4" height="4" rx="1"/>'
        '<rect class="pp-fifo-out" x="20" y="9" width="4" height="4" rx="1"/>'
        '</g></svg>'),
    # Two documents slide together, merge into one, and get a check mark. Animated in dashboard.css.
    "recon": (
        '<span class="pp-logo" title="Click to replay">'
        + _SVG.format(_DOC).replace("<svg ", '<svg class="pp-doc-left" ')
        + _SVG.format(_DOC).replace("<svg ", '<svg class="pp-doc-right" ') +
        '<svg class="pp-doc-merged" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path fill="currentColor" d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<polyline fill="none" points="14 2 14 8 20 8"/>'
        '<path class="pp-check" fill="none" stroke-width="3" d="M9 13l2 2 4-4"/></svg></span>'),
    # A magnifying glass sweeps the list and a transaction line flashes red as it passes.
    "sales-tax": _SVG.format(
        '<path d="M4 6h12 M4 12h16 M4 18h10" stroke="#93c5fd"/>'
        '<path class="pp-flag" d="M12 12h4"/>'
        '<g class="pp-glass"><circle class="pp-mask" cx="9" cy="9" r="4"/><path d="M11.8 11.8L15 15"/></g>'),
    # Invoice -> approval -> payment light up in turn.
    "invoice-hub": _SVG.format(
        '<path d="M6 12h12" stroke="#93c5fd" stroke-dasharray="2 2"/>'
        '<g class="pp-stage pp-stage-1"><rect class="pp-mask" x="2" y="9" width="6" height="4" rx="1"/><path d="M2 9l3 2 3-2"/></g>'
        '<g class="pp-stage pp-stage-2"><circle class="pp-mask" cx="12" cy="12" r="3"/><path d="M10.5 12l1 1 2-2"/></g>'
        '<g class="pp-stage pp-stage-3"><rect class="pp-mask" x="16" y="9" width="6" height="4" rx="1"/><circle cx="19" cy="11" r="1.5"/></g>'),
}


_GENERIC = _SVG.format('<rect x="3" y="3" width="18" height="18" rx="3"/>')


def _card(app) -> str:
    badge = f'<span class="pp-badge">{html.escape(app.badge)}</span>' if app.badge else ""
    return (
        f'<a class="pp-card" href="/{html.escape(app.url_path)}" target="_self">'
        f'<div class="pp-icon" data-icon="{html.escape(app.url_path)}"></div>'
        f'<h3 class="pp-title">{html.escape(app.title)}{badge}</h3>'
        f'<p class="pp-summary">{html.escape(app.summary)}</p>'
        f'<span class="pp-action">{html.escape(app.action)} &rarr;</span></a>'
    )


def _icons_json() -> str:
    # DOMPurify drops a <script> whose text contains "<" followed by a letter, so escape every "<".
    icons = {**{a.url_path: _GENERIC for a in APPS}, **_ICONS}
    return json.dumps(icons).replace("<", "\\u003c")


page = (
    f"<style>{(_HERE / 'dashboard.css').read_text(encoding='utf-8')}</style>"
    '<div class="pp-dash">'
    '<header class="pp-hero"><p class="pp-brand">Panhandle Pure</p>'
    "<h1>Accounting Workspace</h1><p>Pick an application to get started.</p>"
    "</header>"
    '<div class="pp-notice"><strong>Uploaded files:</strong> Switching applications clears uploads '
    "from the application you leave. Open applications in separate browser tabs to retain each session.</div>"
    f'<div class="pp-grid">{"".join(_card(app) for app in APPS)}</div>'
    "</div>"
    f"<script>window.PP_ICONS = {_icons_json()};\n"
    f"{(_HERE / 'dashboard.js').read_text(encoding='utf-8')}</script>"
)
if st.session_state.get(sales_page.VIEW_KEY) == sales_page.PRICING_VIEW:
    sales_page.render()
    st.stop()

st.html(page, unsafe_allow_javascript=True)
# The only way into the sales page: it is not a registered page, so it has no URL or nav entry.
if st.button("See Pricing and Features", icon=":material/sell:", key="pp-open-pricing", type="primary"):
    st.session_state[sales_page.VIEW_KEY] = sales_page.PRICING_VIEW
    st.rerun()
