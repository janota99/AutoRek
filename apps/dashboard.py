"""Dashboard: the landing page, with one card per application, built from the registry in shared/layout.py.

The page is one hand-written HTML block (styled by shared/styles/pages/dashboard.css, animated by dashboard.js) rather
than Streamlit widgets. Each card is a plain link to the app's page; dashboard.js routes the click
through Streamlit's own navigation so the session is kept.

The signed-in person's invoice dashboard lives on the Invoice Lifecycle Hub page and Reviews &
Feedback is its own page in the navigation bar; the account strip (shared/suite_banner.js) is on every page.
"""
import base64
import html
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from apps import sales_page
from apps.sales_page import TIERS
from shared import fiscal_session
from shared.fiscal_calendar import (PERIOD_COUNT, FiscalCalendar, default_calendar, parse_period_end_dates,
                                    schedule_problems, validate)
from shared.layout import APPS
from shared.sample_data import SAMPLE_DIR
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
        '<span class="pp-logo">'
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


def _pills(items) -> str:
    return "".join(f'<span class="pp-pill">{html.escape(i)}</span>' for i in items)


@st.cache_data(show_spinner=False)
def _template_files() -> dict[str, list[tuple[str, str, bytes]]]:
    """Each application's sample templates as (label, file name, bytes), keyed by url_path.

    Files come from sample_data/ (synthetic); the FIFO ones are the app's own blank upload templates. A template that
    cannot be built is left out, so the drawer never offers a download that fails."""
    from apps.fifo_inventory.upload_templates import build_master_grid_template, build_receipts_template
    built = {"fifo_master_grid_template.xlsx": build_master_grid_template,
             "fifo_receipts_template.xlsx": build_receipts_template}
    found: dict[str, list[tuple[str, str, bytes]]] = {}
    for app in APPS:
        for label, name in app.templates:
            try:
                data = built[name]() if name in built else (SAMPLE_DIR / name).read_bytes()
            except Exception:
                continue
            found.setdefault(app.url_path, []).append((label, name, data))
    return found


def _template_buttons(path: str) -> str:
    items = _template_files().get(path, [])
    if not items:
        return '<p class="pp-muted">No sample templates for this application.</p>'
    return '<div class="pp-tpls">' + "".join(
        f'<button type="button" class="pp-tpl" data-tpl="{html.escape(path)}:{i}">'
        f'<span class="pp-tpl-ext">.xlsx</span><span class="pp-tpl-name">{html.escape(label.replace(" (.xlsx)", ""))}</span>'
        '<span class="pp-tpl-go">Download</span></button>' for i, (label, _n, _d) in enumerate(items)) + "</div>"


def _schema_tables(app) -> str:
    if not app.schemas:
        return '<p class="pp-muted">No upload files: this prototype uses simulated invoice mail.</p>'
    return "".join(
        f'<div class="pp-schema"><h5>{html.escape(name)}</h5><ol class="pp-cols">'
        + "".join(f'<li><span class="pp-colno">{n:02d}</span>{html.escape(c)}</li>' for n, c in enumerate(cols, 1))
        + "</ol></div>" for name, cols in app.schemas)


def _pane(app) -> str:
    """One application's slide-over content: workflow steps, column schemas, limits, and sample templates."""
    steps = '<ol class="pp-flow">' + "".join(f"<li>{html.escape(t)}</li>" for t in app.steps) + "</ol>"
    sections = (
        ("Workflow", steps),
        ("Required column schemas", _schema_tables(app)),
        ("Sample templates", _template_buttons(app.url_path)),
        ("Good to know", _list(app.limits) + f'<p class="pp-muted">Plan: {html.escape(_plan_names(app))}. '
                                              "Every application is open in this demo.</p>"),
    )
    body = "".join(f"<section><h4>{label}</h4>{content}</section>" for label, content in sections)
    path = html.escape(app.url_path)
    return (f'<div class="pp-pane" data-pane="{path}" hidden>'
            f'<p class="pp-eyebrow">Module details</p><h3>{html.escape(app.title)}</h3>'
            f'<p class="pp-muted">{html.escape(app.summary)}</p>{body}'
            f'<div class="pp-pane-actions"><a class="pp-action" href="/{path}" target="_self" data-route>'
            f'{html.escape(app.action)} &rarr;</a>'
            f'<a class="pp-ghost" href="/{path}?demo=1" target="_self" data-route>Try demo</a></div></div>')


def _templates_pane() -> str:
    groups = "".join(f"<section><h4>{html.escape(a.title)}</h4>{_template_buttons(a.url_path)}</section>"
                     for a in APPS if a.templates)
    return ('<div class="pp-pane" data-pane="templates" hidden><p class="pp-eyebrow">Sample templates</p>'
            '<h3>Start from a correctly shaped file</h3><p class="pp-muted">Synthetic files in the layout each tool '
            f'expects. They contain no real data.</p>{groups}</div>')


def _card(app) -> str:
    badge = f'<span class="pp-badge">{html.escape(app.badge)}</span>' if app.badge else ""
    path = html.escape(app.url_path)
    # What it takes in and produces is printed on the card, not hidden behind a hover: touch screens have no hover.
    io = (f'<dl class="pp-io"><dt>Takes in</dt><dd>{_pills(app.takes_badges)}</dd>'
          f'<dt>Produces</dt><dd>{_pills(app.makes_badges)}</dd></dl>')
    return (
        f'<div class="pp-card" data-href="/{path}">'
        f'<div class="pp-icon" data-icon="{path}"></div>'
        f'<h3 class="pp-title">{html.escape(app.title)}{badge}</h3>'
        f'<p class="pp-summary">{html.escape(app.summary)}</p>{io}'
        '<div class="pp-actions">'
        f'<a class="pp-action" href="/{path}" target="_self" data-route>{html.escape(app.action)} &rarr;</a>'
        f'<a class="pp-ghost" href="/{path}?demo=1" target="_self" title="Opens with sample data loaded; no company files needed">Try demo</a>'
        f'<button type="button" class="pp-link" data-open-pane="{path}">View details</button></div></div>'
    )


def _icons_json() -> str:
    # DOMPurify drops a <script> whose text contains "<" followed by a letter, so escape every "<".
    icons = {**{a.url_path: _GENERIC for a in APPS}, **_ICONS}
    return json.dumps(icons).replace("<", "\\u003c")


def _templates_json() -> str:
    """The sample files as base64, for dashboard.js to turn into a download (a data: link would be stripped)."""
    files = {path: [{"name": name, "b64": base64.b64encode(data).decode("ascii")} for _l, name, data in items]
             for path, items in _template_files().items()}
    return json.dumps(files).replace("<", "\\u003c")


def _status_bar() -> str:
    """The workspace and plan status pills, with a quiet upgrade button."""
    return (
        '<div class="pp-stats">'
        '<span class="pp-stat"><span class="pp-dot"></span>Workspace <strong>Ready</strong></span>'
        f'<span class="pp-stat">Applications <strong class="pp-num">{len(APPS)} available</strong></span>'
        '<span class="pp-stat pp-plan">Plan <span class="pp-tier">Preview tier</span>'
        '<button type="button" class="pp-upgrade" data-open-plans>Upgrade to Pro <span aria-hidden="true">&#10022;</span></button></span>'
        '</div>'
    )


def _onboarding() -> str:
    """One compact, dismissible bar in place of the three-step panel and the tip banner."""
    return (
        '<div class="pp-onboard" role="note"><p><strong>New to Janota?</strong> '
        '<button type="button" class="pp-link" data-tour>Launch interactive tour</button> or '
        '<button type="button" class="pp-link" data-open-pane="templates">grab sample templates &rarr;</button>'
        '<span class="pp-onboard-hint">Each tool keeps its own files; to keep two loaded, open the second in a new browser tab.</span></p>'
        '<button type="button" class="pp-notice-close" aria-label="Dismiss">&times;</button></div>'
    )


# What is stored where, from the code: uploads live in the browser session; Excel outputs are built in memory
# for download; a few small files are written to the server's disk. Update this if that changes.
_DATA_HANDLING = (
    '<ul class="pp-gov">'
    "<li><strong>Uploads</strong> are held in memory for your browser session only. Leaving an application or closing "
    "the tab clears them.</li>"
    "<li><strong>Excel outputs</strong> are built on request for you to download. With no database configured the "
    "apps keep no copy.</li>"
    "<li><strong>Saved Recon runs</strong> (only when your organization's database is connected): each run's "
    "figures, controls, rule versions, who ran it, and a fingerprint of each uploaded file, plus any workpaper you "
    "prepare and any notes. The uploaded files themselves are never stored. Saved runs cannot be edited or deleted "
    "from the app. Runs on the built-in sample data are never saved.</li>"
    "<li><strong>Saved on the server's disk:</strong> FIFO closed-period snapshots and settings; the Sales Tax trial "
    "balance cache, which everyone using the app shares; and Recon's confirmed vendor aliases, with who confirmed "
    "each one and when. To remove any of these, delete the files from the app's folders.</li>"
    "<li><strong>Access:</strong> the sign-in is simulated in your browser and is not access control. Anyone who can "
    "open this app can open every tool and the stored data above. Where your organization has set up a real sign-in, "
    "it only labels who ran and reviewed things in the Recon run history as verified; it does not restrict access.</li>"
    "<li><strong>Demo data</strong> is synthetic: made-up vendors, customers, and amounts.</li></ul>"
)

# Only claims the code backs up: no certification or encryption is asserted here (see _DATA_HANDLING for the detail).
_TRUST_BADGES = ("Uploads held in session memory", "Outputs built on request", "Sample data is synthetic",
                 "Sign-in is a preview, not access control")


def _trust_footer() -> str:
    return (
        '<footer class="pp-trust"><div class="pp-trust-badges">'
        + "".join(f'<span class="pp-shield"><span aria-hidden="true">&#10003;</span>{html.escape(t)}</span>' for t in _TRUST_BADGES)
        + '</div><button type="button" class="pp-link" data-open-pane="governance">Security &amp; Data Governance &rarr;</button></footer>'
    )


def _drawer() -> str:
    governance = ('<div class="pp-pane" data-pane="governance" hidden><p class="pp-eyebrow">Trust</p>'
                  f'<h3>Security &amp; Data Governance</h3>{_DATA_HANDLING}</div>')
    return ('<div class="pp-scrim" hidden></div>'
            '<aside class="pp-drawer" role="dialog" aria-modal="true" aria-label="Details" hidden tabindex="-1">'
            '<button type="button" class="pp-drawer-close" aria-label="Close details">&times;</button>'
            f'<div class="pp-drawer-body">{"".join(_pane(a) for a in APPS)}{_templates_pane()}{governance}</div></aside>')


page = (
    f"{style_tag('dashboard')}"
    '<div class="pp-dash"><div class="pp-main">'
    '<header class="pp-hero" id="pp-overview"><p class="pp-brand">Janota Fin Automations</p>'
    f'<h1>Accounting Workspace</h1><p>Choose an application to get started.</p>{_status_bar()}</header>'
    f"{_onboarding()}"
    '<h2 class="pp-section" id="pp-apps">Applications</h2>'
    f'<div class="pp-grid">{"".join(_card(app) for app in APPS)}</div>'
    f"{_trust_footer()}"
    f"</div>{_drawer()}</div>"
    f"<script>window.PP_ICONS = {_icons_json()};window.PP_TEMPLATES = {_templates_json()};\n"
    f"{(_HERE / 'dashboard.js').read_text(encoding='utf-8')}</script>"
)
if st.session_state.get(sales_page.VIEW_KEY) == sales_page.PRICING_VIEW:
    sales_page.render()
    st.stop()



def _apply_pasted_dates(resets: int) -> None:
    """Callback for "Use these dates": fill the thirteen date boxes from the pasted text, or say what is wrong with it."""
    dates, problem = parse_period_end_dates(st.session_state.get(f"pp-fiscal-paste-{resets}", ""))
    st.session_state[f"pp-fiscal-paste-error-{resets}"] = problem
    if dates:
        for number, day in enumerate(dates, start=1):
            st.session_state[f"pp-fiscal-end-{number}-{resets}"] = day


def _calendar_editor(calendar: FiscalCalendar) -> None:
    """Fiscal calendar setup: year and Period 1 start fill in the thirteen period ends; any date can be adjusted or pasted.

    What is validated and saved is exactly what the date boxes show. Changing the year or start regenerates the dates (a year
    already saved with that start keeps its saved dates); changing Period 13's length changes only Period 13."""
    resets = st.session_state.get("pp-fiscal-resets", 0)
    years = fiscal_session.saved_years()
    if len(years) > 1:
        chosen = st.selectbox("Saved fiscal years", years, index=years.index(calendar.year) if calendar.year in years else 0,
                              key=f"pp-fiscal-saved-{resets}",
                              help="Each year's calendar is kept. Choosing one shows it here; nothing is changed or lost.")
        if chosen != calendar.year and fiscal_session.use_calendar(chosen):
            st.session_state["pp-fiscal-resets"] = resets + 1
            st.rerun()
    ky, ks, kp, kb = (f"pp-fiscal-year-{resets}", f"pp-fiscal-start-{resets}", f"pp-fiscal-p13-{resets}",
                      f"pp-fiscal-basis-{resets}")
    end_key = lambda n: f"pp-fiscal-end-{n}-{resets}"      # noqa: E731
    weeks = {"4 weeks": 28, "5 weeks": 35}
    if ky not in st.session_state:                          # first sight of this calendar: show exactly what is saved
        st.session_state[ky], st.session_state[ks] = calendar.year, calendar.start
        st.session_state[kp] = "5 weeks" if (calendar.period_ends[12] - calendar.period_ends[11]).days == 35 else "4 weeks"
        for number, day in enumerate(calendar.period_ends, start=1):
            st.session_state[end_key(number)] = day
        st.session_state[kb] = (calendar.year, calendar.start, st.session_state[kp])
    # A change is read from the widgets' stored values *before* they are drawn, so the dates (and the Period 13 choice) can be
    # updated in the same run.
    year, start, p13 = st.session_state[ky], st.session_state[ks], st.session_state[kp]
    old_year, old_start, old_p13 = st.session_state[kb]
    if (year, start) != (old_year, old_start):             # a different year or start: offer its dates afresh
        fresh = fiscal_session.suggested_ends(int(year), start)
        for number, day in enumerate(fresh, start=1):
            st.session_state[end_key(number)] = day
        p13 = st.session_state[kp] = "5 weeks" if (fresh[12] - fresh[11]).days == 35 else "4 weeks"
    elif p13 != old_p13:                                   # only Period 13's length changed
        st.session_state[end_key(13)] = st.session_state[end_key(12)] + timedelta(days=weeks[p13])
    st.session_state[kb] = (year, start, p13)
    year_col, start_col, p13_col = st.columns([1, 1.3, 1.4])
    year = int(year_col.number_input("Fiscal year", 1900, 2199, step=1, key=ky))
    start = start_col.date_input("Period 1 starts", min_value=date(2000, 1, 1), max_value=date(2100, 12, 31),
                                 format="YYYY-MM-DD", key=ks)
    p13_col.radio("Period 13 length", list(weeks), horizontal=True, key=kp,
                  help="Most years have 13 four-week periods. A 53-week year has a five-week Period 13.")

    with st.expander("Adjust individual dates", expanded=False):
        left, right = st.columns(2)
        for number in range(1, PERIOD_COUNT + 1):
            (left if number <= 7 else right).date_input(
                f"Period {number:02d} ends", min_value=date(2000, 1, 1), max_value=date(2100, 12, 31),
                format="YYYY-MM-DD", key=end_key(number))
    with st.expander("Paste all 13 dates", expanded=False):
        st.text_area("Period end dates", key=f"pp-fiscal-paste-{resets}", height=120,
                     placeholder="One per line, or copy a spreadsheet column: 2025-10-25, 2025-11-22, ...",
                     help="Thirteen dates, written 2025-10-25 or 10/25/2025.")
        st.button("Use these dates", key=f"pp-fiscal-paste-go-{resets}", on_click=_apply_pasted_dates, args=(resets,))
        if st.session_state.get(f"pp-fiscal-paste-error-{resets}"):
            st.error(st.session_state[f"pp-fiscal-paste-error-{resets}"])

    ends = tuple(st.session_state[end_key(n)] for n in range(1, PERIOD_COUNT + 1))
    problems = schedule_problems(start, ends)
    st.caption("Period 01 " + f"{start:%Y-%m-%d}" + " to " + f"{ends[0]:%Y-%m-%d}" + "; Period 13 ends " + f"{ends[-1]:%Y-%m-%d}"
               + f" ({(ends[-1] - ends[-2]).days // 7} weeks long).")
    for problem in problems:
        st.error(problem)
    if st.button("Save calendar", type="primary", icon=":material/save:", disabled=bool(problems), key=f"pp-fiscal-save-{resets}"):
        fiscal_session.set_calendar(FiscalCalendar(year, start, ends))
        st.session_state["pp-fiscal-resets"] = resets + 1   # fresh widgets that show what was saved
        st.session_state["pp-fiscal-flash"] = True
        st.rerun()
    if st.session_state.pop("pp-fiscal-flash", False):
        st.success("Calendar saved for future sessions.", icon=":material/check_circle:")


with st.container(key="pp-fiscal"):
    stored_end = fiscal_session.get_end_date()
    field, calendar_slot, status = st.columns([1.2, 1, 3], vertical_alignment="bottom")
    entered = field.date_input(
        "Fiscal period ending date (optional)", value=stored_end, format="YYYY-MM-DD", key="pp-fiscal-end",
        help="Fills in the fiscal year, period, and period end date in the tools that ask for them. Leave blank to choose inside each tool.",
    )
    with calendar_slot.popover("Fiscal calendar", icon=":material/calendar_month:"):
        _calendar_editor(fiscal_session.get_calendar())
    fiscal_session.set_end_date(entered)
    if fiscal_session.save_failed():
        st.warning("These settings could not be saved to disk, so they will be gone next session.")
    context = fiscal_session.current()
    if context is None:
        status.caption("No date set: each tool keeps its own fiscal period controls.")
    elif context.period is None:
        status.warning(f"{context.end_date:%Y-%m-%d} is outside the fiscal calendar, so only the end date is passed on. "
                       "Open Fiscal calendar to adjust it.", icon=":material/event_busy:")
    else:
        status.success(f"FY{context.fiscal_year} Period {context.period:02d}, ending {context.end_date:%Y-%m-%d}. "
                       "Set in Inventory Costing and Data Reconciliation; change it there any time.",
                       icon=":material/event_available:")

st.html(page, unsafe_allow_javascript=True)
# The only way into the sales page: it is not a registered page, so it has no URL or nav entry.
# base/chrome.css pins this button in the top-right corner and suite_banner.js keeps it left of the account strip.
if st.button("View Plans", icon=":material/auto_awesome:", key="pp-open-pricing"):
    st.session_state[sales_page.VIEW_KEY] = sales_page.PRICING_VIEW
    st.rerun()
