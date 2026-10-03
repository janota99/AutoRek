"""Dashboard: the landing page, with one card per application, built from the registry in shared/layout.py.

The page is one hand-written HTML block (styled by dashboard.css, animated by dashboard.js) rather
than Streamlit widgets. Each card is a plain link to the app's page; dashboard.js routes the click
through Streamlit's own navigation so the session is kept.
"""
import html
import json
from pathlib import Path

import streamlit as st

from shared.layout import APPS

_HERE = Path(__file__).resolve().parent

_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>'
_DOC = '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>'

# Card icons, keyed by the app's url_path. Streamlit strips inline SVG from HTML, so dashboard.js
# inserts these into each card's .pp-icon tile. An app without an entry gets a generic tile.
_ICONS = {
    "fifo-inventory": _SVG.format(
        '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/>'
        '<path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/><path d="M8 3.5 16 8"/>'),
    # Two documents slide together, merge into one, and get a check mark. Animated in dashboard.css.
    "recon": (
        '<span class="pp-logo" title="Click to replay">'
        + _SVG.format(_DOC).replace("<svg ", '<svg class="pp-doc-left" ')
        + _SVG.format(_DOC).replace("<svg ", '<svg class="pp-doc-right" ') +
        '<svg class="pp-doc-merged" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path fill="currentColor" d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<polyline fill="none" points="14 2 14 8 20 8"/>'
        '<path class="pp-check" fill="none" stroke-width="3" d="M9 13l2 2 4-4"/></svg></span>'),
    "sales-tax": _SVG.format(
        '<path d="M4 2v20l2-2 2 2 2-2 2 2 2-2 2 2 2-2 2 2V2l-2 2-2-2-2 2-2-2-2 2-2-2-2 2Z"/>'
        '<path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><path d="M12 17V7"/>'),
    "invoice-hub": _SVG.format(
        '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>'
        '<circle cx="17" cy="17" r="4" fill="#ffffff"/><path d="m15 17 1.5 1.5 2.5-2.5" stroke="#16a34a"/>'),
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
    "<h1>Accounting Workspace</h1><p>Pick an application to get started.</p></header>"
    '<div class="pp-notice"><strong>Uploaded files:</strong> Switching applications clears uploads '
    "from the application you leave. Open applications in separate browser tabs to retain each session.</div>"
    f'<div class="pp-grid">{"".join(_card(app) for app in APPS)}</div></div>'
    f"<script>window.PP_ICONS = {_icons_json()};\n"
    f"{(_HERE / 'dashboard.js').read_text(encoding='utf-8')}</script>"
)
st.html(page, unsafe_allow_javascript=True)
