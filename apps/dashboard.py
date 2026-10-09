"""Dashboard: the landing page, with one card per application, built from the registry in shared/layout.py.

The page is one hand-written HTML block (styled by shared/styles/pages/dashboard.css, animated by dashboard.js) rather
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
from apps.sales_page import TIERS
from shared.layout import APPS
from shared.styles import style_tag

_HERE = Path(__file__).resolve().parent

_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>'
_DOC = '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>'

# Card icons, keyed by the app's url_path. Streamlit strips inline SVG from HTML, so dashboard.js
# inserts these into each card's .pp-icon tile. An app without an entry gets a generic tile.
_ICONS = {
    # Boxes advance along a track toward the arrow, first in, first out: a new box fades in on the
    # left while the oldest fades out on the right. Animated in shared/styles/pages/dashboard.css.
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
    # Two documents slide together, merge into one, and get a check mark. Animated in shared/styles/pages/dashboard.css.
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


def _plan_names(app) -> str:
    """The plans that include an application, lowest first (from the Plans page)."""
    names = [t.name for t in TIERS if app.url_path in t.tool_ids]
    first = next(i for i, t in enumerate(TIERS) if app.url_path in t.tool_ids)
    return f"{names[0]} and above" if first < len(TIERS) - 1 else f"{names[0]} only"


def _list(items) -> str:
    return "<ul>" + "".join(f"<li>{html.escape(i)}</li>" for i in items) + "</ul>"


def _details(app) -> str:
    """The "View details" drawer on a card: what it accepts, needs, produces, and cannot do."""
    sections = (
        ("Supported files", app.files), ("Required inputs", app.inputs),
        ("Example outputs", app.outputs), ("Limitations", app.limits),
    )
    body = "".join(f"<h4>{label}</h4>{_list(items)}" for label, items in sections if items)
    body += f"<h4>Plan availability</h4><p>{html.escape(_plan_names(app))}. Every application is open in this demo.</p>"
    return f'<div class="pp-details" hidden>{body}</div>'


_RAIL = (
    ("Overview", "pp-overview", True),
    ("Applications", "pp-apps", True),
    ("Projects", "", False),
    ("Reports", "", False),
    ("Settings", "", False),
)


def _rail() -> str:
    items = "".join(
        (f'<a href="#{anchor}" data-scroll="{anchor}">{label}</a>' if live
         else f'<span class="pp-rail-off" aria-disabled="true">{label}<em>Soon</em></span>')
        for label, anchor, live in _RAIL
    )
    return f'<nav class="pp-rail" aria-label="Workspace">{items}</nav>'


def _card(app) -> str:
    badge = f'<span class="pp-badge">{html.escape(app.badge)}</span>' if app.badge else ""
    path = html.escape(app.url_path)
    # The hover preview (dashboard.js) reads these as data, not as markup.
    facts = json.dumps({"title": app.title, "inputs": list(app.inputs), "outputs": list(app.outputs)})
    return (
        f'<div class="pp-card" data-href="/{path}" data-facts="{html.escape(facts, quote=True)}">'
        f'<div class="pp-icon" data-icon="{path}"></div>'
        f'<h3 class="pp-title">{html.escape(app.title)}{badge}</h3>'
        f'<p class="pp-summary">{html.escape(app.summary)}</p>'
        '<div class="pp-actions">'
        f'<a class="pp-action" href="/{path}" target="_self" data-route>{html.escape(app.action)} &rarr;</a>'
        f'<a class="pp-ghost" href="/{path}?demo=1" target="_self" title="Opens with sample data loaded; no company files needed">Try demo</a>'
        '<button type="button" class="pp-link" aria-expanded="false">View details</button></div>'
        f'{_details(app)}</div>'
    )


def _icons_json() -> str:
    # DOMPurify drops a <script> whose text contains "<" followed by a letter, so escape every "<".
    icons = {**{a.url_path: _GENERIC for a in APPS}, **_ICONS}
    return json.dumps(icons).replace("<", "\\u003c")


_GET_STARTED = (
    '<section class="pp-panel pp-start" aria-labelledby="pp-start-h"><h2 id="pp-start-h">Get started</h2>'
    '<ol class="pp-steps">'
    "<li><strong>Try a demo.</strong> Choose <em>Try demo</em> on any application below. It opens with sample data "
    "already loaded, so you can see the workflow without company files.</li>"
    "<li><strong>Download a template.</strong> Each application has a <em>Download Sample Templates</em> drawer "
    "showing the file layout it expects.</li>"
    "<li><strong>Run it on your own files.</strong> Upload your exports, run the application, and download the "
    "Excel workbook it produces.</li></ol>"
    '<p class="pp-muted">Fiscal period is set inside each application, so a period is never applied to the wrong tool.</p>'
    "</section>"
)

# What is stored where, from the code: uploads live in the browser session; Excel outputs are built in memory
# for download; a few small files are written to the server's disk. Update this if that changes.
_DATA_HANDLING = (
    '<details class="pp-panel pp-data"><summary>How your data is handled</summary><ul>'
    "<li><strong>Uploads</strong> are held in memory for your browser session only. Leaving an application or closing "
    "the tab clears them.</li>"
    "<li><strong>Excel outputs</strong> are built on request for you to download. The apps keep no copy.</li>"
    "<li><strong>Saved on the server's disk:</strong> FIFO closed-period snapshots and settings; the Sales Tax trial "
    "balance cache, which everyone using the app shares; and Recon's confirmed vendor aliases, with who confirmed "
    "each one and when. To remove any of these, delete the files from the app's folders.</li>"
    "<li><strong>Access:</strong> the sign-in is simulated in your browser and is not access control. Anyone who can "
    "open this app can open every tool and the stored data above.</li>"
    "<li><strong>Demo data</strong> is synthetic: made-up vendors, customers, and amounts.</li></ul></details>"
)

page = (
    f"{style_tag('dashboard')}"
    '<div class="pp-dash"><div class="pp-shell">'
    f"{_rail()}"
    '<div class="pp-main">'
    '<header class="pp-hero" id="pp-overview"><p class="pp-brand">Janota Fin Automatations</p>'
    '<h1>Accounting Workspace</h1><p>Pick an application to get started.</p></header>'
    '<div class="pp-stats">'
    '<span class="pp-stat"><span class="pp-dot"></span>Session <strong>Active</strong></span>'
    f'<span class="pp-stat">Applications <strong>{len(APPS)} available</strong></span>'
    '<span class="pp-stat">Access <strong>Demo access</strong>'
    '<button type="button" class="pp-link" data-open-plans>Compare plans</button></span></div>'
    '<div class="pp-notice" role="note"><span class="pp-notice-icon" aria-hidden="true">i</span>'
    '<p><strong>Uploaded files:</strong> Switching applications clears uploads '
    "from the application you leave. Open applications in separate browser tabs to retain each session.</p>"
    '<button type="button" class="pp-notice-close" aria-label="Dismiss notice">&times;</button></div>'
    f"{_GET_STARTED}"
    '<h2 class="pp-section" id="pp-apps">Applications</h2>'
    f'<div class="pp-grid">{"".join(_card(app) for app in APPS)}</div>'
    '<div class="pp-preview" role="status" aria-live="polite">'
    '<span class="pp-preview-hint">Hover over an application to preview what it takes in and produces.</span></div>'
    f"{_DATA_HANDLING}"
    "</div></div></div>"
    f"<script>window.PP_ICONS = {_icons_json()};\n"
    f"{(_HERE / 'dashboard.js').read_text(encoding='utf-8')}</script>"
)
if st.session_state.get(sales_page.VIEW_KEY) == sales_page.PRICING_VIEW:
    sales_page.render()
    st.stop()

st.html(page, unsafe_allow_javascript=True)
# The only way into the sales page: it is not a registered page, so it has no URL or nav entry.
# base/chrome.css pins this button in the top-right corner and suite_banner.js keeps it left of the account strip.
if st.button("View Plans", icon=":material/auto_awesome:", key="pp-open-pricing"):
    st.session_state[sales_page.VIEW_KEY] = sales_page.PRICING_VIEW
    st.rerun()
