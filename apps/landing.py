"""Front landing page: Understand the value, explore solutions, see it work, choose a plan, enter the workspace.

Sections 1-3 (opening, solution explorer, walkthrough) and 7-9 (FAQ and background, closing, footer) are hand-written
HTML blocks styled by shared/styles/pages/landing.css and driven by landing.js. Sections 5 (deliverables) and 6 (plans
and the purchase sequence) are Streamlit widgets, because they download files and keep order state. There is no second
site header: while this page shows, landing.css hides Streamlit's page links and the small public bar (_nav) takes their
place beside the Janota FIN wordmark that st.logo draws. Every other page keeps the workspace navigation.

Every example number comes from apps/landing_data.py. The checkout is a labeled simulation: no processor is
connected, nothing is charged, and the account step creates a session-only demo account (login, password kept only as a salted hash, offline authenticator code).
"""
from __future__ import annotations

import base64
import functools
import html
import re
from pathlib import Path

import streamlit as st

from apps import landing_data as data
from apps import sales_page
from apps.sales_page import TIERS, _ANNUAL, _CYCLE_KEY, _MONTHLY
from apps.demo_banner import SECTION_PARAM, SOLUTIONS_SECTION
from shared import billing
from shared.layout import _LOGO_PATH, APPS, DASHBOARD_SCRIPT
from shared.styles import style_tag

_HERE = Path(__file__).resolve().parent
_E = html.escape

# Session keys for the purchase sequence.
STEP = "pp_l_step"          # 1 plan, 2 review, 3 account, 4 checkout, 5 confirmation, 6 onboarding
PLAN = "pp_l_plan"
ACCOUNT = "pp_l_account"    # the signed-in demo account: names, email, login, password hash, authenticator secret
ACCOUNTS = "pp_l_accounts"  # demo accounts created in this session, by lower-case login name
PENDING = "pp_l_pending"    # an account waiting for its first authenticator code
ANNUAL = "pp_l_annual"
TEMPLATE = "pp_l_template"
_STEPS = ("Plan", "Review", "Account", "Checkout", "Confirmation", "Set up")


# --- shared HTML pieces --------------------------------------------------------------------------------------------

def _table(header, rows, *, amount_cols=(), cls="", caption="") -> str:
    head = "".join(f"<th scope='col'{' class=\"num\"' if i in amount_cols else ''}>{_E(h)}</th>" for i, h in enumerate(header))
    body = ""
    for k, row in enumerate(rows):
        cells = row["cells"] if isinstance(row, dict) else row
        tone = f" data-tone=\"{row['tone']}\"" if isinstance(row, dict) and row.get("tone") else ""
        tds = "".join(f"<td{' class=\"num\"' if i in amount_cols else ''}>{_E(str(c))}</td>" for i, c in enumerate(cells))
        body += f"<tr style=\"--i:{k}\"{tone}>{tds}</tr>"
    cap = f"<caption>{_E(caption)}</caption>" if caption else ""
    return f'<table class="pp-l-table {cls}">{cap}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _icon(name: str) -> str:
    """A Material Symbols glyph (the font Streamlit already loads), the same icon the registry gives the tool's page."""
    return f'<span class="material-symbols-rounded pp-l-ico" aria-hidden="true">{_E(name)}</span>'


def _kpis(items) -> str:
    out = ""
    for label, value, tone in items:
        out += (f'<div class="pp-l-kpi" data-tone="{tone}"><span class="pp-l-kpi-v" data-final="{_E(value)}">{_E(value)}</span>'
                f'<span class="pp-l-kpi-l">{_E(label)}</span></div>')
    return f'<div class="pp-l-kpis">{out}</div>'


def _chips(*labels, accent=None) -> str:
    return "".join(f'<span class="pp-l-chip"{" data-accent" if label == accent else ""}>{_E(label)}</span>' for label in labels)


# --- the three worked examples ------------------------------------------------------------------------------------------

def _recon_view() -> dict:
    r = data.reconcile()
    qb, inf = len(data.QB_ROWS), len(data.INF_ROWS)
    kpis = _kpis((
        ("Matched to the cent", str(len(r["matched"])), "pass"),
        ("Open items to review", str(len(r["exceptions"])), "review"),
        ("Matched value", data.money(r["matched_cents"]), "neutral"),
    ))
    rows = [{"cells": (q, i, c, po, data.money(a)), "tone": "pass"} for q, i, c, po, a in r["matched"]]
    exc = [{"cells": (q or "-", i or "-", c, po, data.money(a)), "tone": "review", "why": why}
           for q, i, c, po, a, why in r["exceptions"]]
    results = _table(("QuickBooks", "Infinium", "Customer", "PO", "Amount"), exc + rows, amount_cols=(4,),
                     caption="Reconciliation result: open items first")
    why = "".join(f"<li><strong>{_E(e['cells'][3])}</strong> {_E(e['why'])}</li>" for e in exc)
    inputs = (_table(("Row", "Customer", "PO", "Amount"), [(a, b, c, data.money(d)) for a, b, c, d in data.QB_ROWS],
                     amount_cols=(3,), caption=f"QuickBooks sales ({qb} rows, {data.money(r['qb_total'])})")
              + _table(("Row", "Customer", "PO", "Amount"), [(a, b, c, data.money(d)) for a, b, c, d in data.INF_ROWS],
                       amount_cols=(3,), caption=f"Infinium sales ({inf} rows, {data.money(r['inf_total'])})"))
    flow = (f'<div class="pp-l-flow">{_chips(f"QuickBooks: {qb} rows", "Mapped fields: PO + Amount", f"Infinium: {inf} rows")}'
            f'<span class="pp-l-arrow" aria-hidden="true"></span>{_chips(f"{len(r['matched'])} matched", f"{len(r['exceptions'])} open", accent=f"{len(r['exceptions'])} open")}</div>')
    return {"flow": flow, "kpis": kpis, "results": results, "inputs": inputs, "why": f'<ul class="pp-l-why">{why}</ul>'}


def _fifo_view() -> dict:
    f = data.fifo()
    kpis = _kpis((
        ("Cost of usage", data.money(f["cost_of_usage"]), "neutral"),
        ("Ending inventory", data.money(f["ending_value"]), "pass"),
        ("Controls", "PASS", "pass"),
    ))
    usage = [{"cells": ("Used", n, f"{u:,}", data.money(c)), "tone": "neutral"} for n, u, c in f["usage"]]
    ending = [{"cells": ("Remaining", n, f"{u:,}", data.money(v)), "tone": "pass"} for n, u, v in f["ending"]]
    results = _table(("Section", "Layer", "Units", "Value"), usage + ending, amount_cols=(2, 3),
                     caption="FIFO costing: oldest layers are used first")
    inputs = _table(("Layer", "Units", "Value"), [(n, f"{u:,}", data.money(v)) for n, u, v in data.LAYERS],
                    amount_cols=(1, 2), caption=f"Opening balance and receipts ({data.money(f['available_value'])}); "
                    f"{data.UNITS_USED:,} units used this period")
    flow = (f'<div class="pp-l-flow">{_chips("Opening balance", "Receipts", f"{data.UNITS_USED:,} units used")}'
            f'<span class="pp-l-arrow" aria-hidden="true"></span>{_chips("FIFO layers", "Cost summary", "Period report")}</div>')
    tie = (f"{data.money(f['available_value'])} available - {data.money(f['cost_of_usage'])} used = "
           f"{data.money(f['ending_value'])} ending.")
    return {"flow": flow, "kpis": kpis, "results": results, "inputs": inputs, "why": f'<p class="pp-l-tie">{_E(tie)}</p>'}


def _review_view() -> dict:
    v = data.review()
    ready = len(v["queues"].get("Ready", []))
    kpis = _kpis((
        ("Ready to post", str(ready), "pass"),
        ("Need a decision", str(v["rows"] - ready), "review"),
        ("Dollar control", data.money(v["control_diff"]), "pass"),
    ))
    rows = []
    for queue in ("Needs vendor mapping", "Review sales tax", "Ready"):
        for raw, amount, vendor, _ in v["queues"].get(queue, []):
            rows.append({"cells": (raw, vendor or "-", queue, data.money(amount)), "tone": "pass" if queue == "Ready" else "review"})
    results = _table(("Original text", "Standard vendor", "Queue", "Amount"), rows, amount_cols=(3,),
                     caption="Standardized transactions, organized by review queue")
    inputs = _table(("Raw transaction text", "Amount"), [(r, data.money(a)) for r, a, _, _ in data.TXN_ROWS],
                    amount_cols=(1,), caption=f"Raw transactions ({v['rows']} rows, {data.money(v['total'])})")
    flow = (f'<div class="pp-l-flow">{_chips("Raw transactions", "Vendor mapping")}'
            f'<span class="pp-l-arrow" aria-hidden="true"></span>{_chips("Standard vendors", "Review queues", "Cleaned export")}</div>')
    note = f"Cleaned total {data.money(v['total'])} equals source total {data.money(v['total'])}: difference {data.money(v['control_diff'])}."
    return {"flow": flow, "kpis": kpis, "results": results, "inputs": inputs, "why": f'<p class="pp-l-tie">{_E(note)}</p>'}


_VIEWS = {"recon": _recon_view, "fifo": _fifo_view, "review": _review_view}


def _explorer() -> str:
    tabs, panels = "", ""
    for n, sol in enumerate(data.SOLUTIONS):
        sel = n == 0
        tabs += (f'<button type="button" role="tab" class="pp-l-tab" id="pp-l-tab-{sol.id}" aria-controls="pp-l-panel-{sol.id}" '
                 f'aria-selected="{str(sel).lower()}" tabindex="{0 if sel else -1}" data-tab="{sol.id}">{_icon(sol.icon)}{_E(sol.tab)}</button>')
        v = _VIEWS[sol.id]()
        benefits = "".join(f"<li>{_E(b)}</li>" for b in sol.benefits)
        panels += (
            f'<div role="tabpanel" class="pp-l-panel" id="pp-l-panel-{sol.id}" aria-labelledby="pp-l-tab-{sol.id}" data-mode="results"'
            f'{"" if sel else " hidden"}>'
            f'<div class="pp-l-panel-side"><h3>{_icon(sol.icon)}{_E(sol.name)}</h3><p class="pp-l-lede">{_E(sol.tagline)}</p>'
            f'<ul class="pp-l-benefits">{benefits}</ul><p class="pp-l-note">{_E(sol.example_note)} Sample data.</p>'
            f'<div class="pp-l-actions"><a class="pp-l-btn pp-l-btn-primary" href="/{_E(sol.app_path)}?demo=1" target="_self">'
            f'Try {_E(sol.tab)}</a></div></div>'
            f'<div class="pp-l-panel-main">{v["flow"]}'
            f'<div class="pp-l-demo-bar" role="group" aria-label="Example view">'
            f'<button type="button" class="pp-l-seg" data-mode="results" aria-pressed="true">Results</button>'
            f'<button type="button" class="pp-l-seg" data-mode="inputs" aria-pressed="false">Inspect inputs</button>'
            f'<button type="button" class="pp-l-seg pp-l-rerun" data-rerun>Rerun example</button></div>'
            f'<div class="pp-l-view" data-view="results">{v["kpis"]}<div class="pp-l-scroll">{v["results"]}</div>{v["why"]}</div>'
            f'<div class="pp-l-view" data-view="inputs" hidden><div class="pp-l-scroll">{v["inputs"]}</div></div>'
            f'</div></div>'
        )
    return (
        '<section class="pp-l-section" id="pp-l-solutions" aria-labelledby="pp-l-sol-h">'
        '<p class="pp-l-eyebrow">Explore solutions</p><h2 id="pp-l-sol-h">Pick the work you do. See the result first.</h2>'
        f'<div class="pp-l-tabs" role="tablist" aria-label="Solutions">{tabs}</div>{panels}</section>'
    )


def _hero() -> str:
    r = data.reconcile()
    exc = r["exceptions"]
    qb, inf = len(data.QB_ROWS), len(data.INF_ROWS)
    lines = "".join(
        f'<li data-tone="review"><span>{_E(c)} &middot; PO {_E(po)}</span><b>{_E(data.money(a))}</b><em>{_E(why)}</em></li>'
        for q, i, c, po, a, why in exc)
    return (
        '<header class="pp-l-hero pp-l-band pp-l-dark"><div class="pp-l-hero-copy">'
        '<p class="pp-l-eyebrow">Flexible accounting automation</p>'
        '<h1>Automate the <em>matching</em>. Review the exceptions.</h1>'
        '<p class="pp-l-lede">Janota FIN sets up repeatable workflows for reconciliation, inventory costing, and '
        'transaction review. Routine records match automatically; the few that need a person arrive with the reason attached.</p>'
        '<div class="pp-l-actions">'
        '<button type="button" class="pp-l-btn pp-l-btn-light" data-demo>Try Interactive Demo</button>'
        '<button type="button" class="pp-l-btn pp-l-btn-outline" data-scroll="pp-l-pricing">Explore Plans</button></div>'
        '<ul class="pp-l-proof"><li>Amounts agree to the cent</li><li>Nothing is guessed</li><li>Every figure traces to a source row</li></ul>'
        '</div>'
        '<div class="pp-l-stage" aria-label="Product preview (sample data)">'
        '<div class="pp-l-sources">'
        f'<div class="pp-l-source"><b>QuickBooks</b><span>{qb} sales rows</span></div>'
        f'<div class="pp-l-source"><b>Infinium</b><span>{inf} sales rows</span></div></div>'
        '<div class="pp-l-conn" aria-hidden="true"></div>'
        '<div class="pp-l-engine"><span class="pp-l-pulse" aria-hidden="true"></span>Exact-cent matching</div>'
        '<div class="pp-l-conn pp-l-conn-down" aria-hidden="true"></div>'
        '<div class="pp-l-hero-card">'
        '<div class="pp-l-card-top"><span class="pp-l-sample-tag">Interactive sample</span>'
        '<b>Reconciliation &middot; example template</b></div>'
        f'{_kpis((("Matched to the cent", str(len(r["matched"])), "pass"), ("Open items", str(len(r["exceptions"])), "review"), ("Matched value", data.money(r["matched_cents"]), "neutral")))}'
        f'<ul class="pp-l-open">{lines}</ul>'
        '<p class="pp-l-note">Sample data. <button type="button" class="pp-l-link" data-demo>Inspect the inputs and rerun it</button>.</p>'
        '</div></div></header>'
    )


_STEP_ICONS = ("upload_file", "account_tree", "tune", "fact_check")  # a small visual per step (Streamlit strips inline SVG)


def _walkthrough() -> str:
    steps, detail = "", ""
    for n, s in enumerate(data.WALKTHROUGH, 1):
        steps += (f'<li class="pp-l-step" data-reveal style="--d:{(n - 1) * 90}ms"><div class="pp-l-art">{_icon(_STEP_ICONS[n - 1])}</div>'
                  f'<span class="pp-l-step-n">Step {n}</span><h3>{_E(s.title)}</h3><p>{_E(s.blurb)}</p></li>')
        now = "".join(f"<li>{_E(x)}</li>" for x in s.available)
        planned = "".join(f"<li>{_E(x)}</li>" for x in s.planned)
        detail += (f'<div class="pp-l-cap"><h4>{n}. {_E(s.title)}</h4>'
                   f'<p><span class="pp-l-tag" data-kind="now">Available now</span></p><ul>{now}</ul>'
                   f'<p><span class="pp-l-tag" data-kind="planned">Planned</span></p><ul class="pp-l-planned">{planned}</ul></div>')
    return (
        '<section class="pp-l-section" id="pp-l-how" aria-labelledby="pp-l-how-h">'
        '<p class="pp-l-eyebrow">How it works</p><h2 id="pp-l-how-h">Adapt it to your data in four steps</h2>'
        f'<ol class="pp-l-steps">{steps}</ol>'
        '<details class="pp-l-detail"><summary>See what is available now and what is planned</summary>'
        f'<div class="pp-l-caps">{detail}</div>'
        '<p class="pp-l-note">Detailed configuration happens during setup and inside the workspace. '
        'Items marked Planned are not built yet.</p></details></section>'
    )


_FORMATS = (  # (what you bring, status). Status is honest: nothing planned is shown as available.
    ("QuickBooks sales export", "now"), ("Infinium sales export", "now"), ("Excel (.xlsx) and CSV uploads", "now"),
    ("Receipts and ending-inventory grids", "now"), ("Raw transactions and a vendor mapping", "now"),
    ("Vendor lists, previous and updated", "now"), ("Historical sales files", "now"),
    ("Direct QuickBooks connection", "planned"), ("Outlook invoice mail", "prototype"),
)
_TAG_TEXT = {"now": "Available now", "planned": "Planned", "prototype": "Prototype"}


def _marquee() -> str:
    chips = "".join(f'<span class="pp-l-mq-chip" data-kind="{k}">{_E(label)}</span>' for label, k in _FORMATS)
    return ('<section class="pp-l-marquee" aria-label="Files and sources it works with">'
            '<p class="pp-l-mq-label">Starts from the files you already have</p>'
            f'<div class="pp-l-mq-track"><div>{chips}</div><div aria-hidden="true">{chips}</div></div></section>')


def _impact() -> str:
    """Dark band of figures. Every number is computed from the sample data on this page; none is a customer claim."""
    r, f, v = data.reconcile(), data.fifo(), data.review()
    near = next((m.group(1) for e in r["exceptions"] if (m := re.search(r"differs by (\$[\d,.]+)", e[5]))), "$0.00")
    ready = len(v["queues"].get("Ready", []))
    stats = (
        (str(len(r["matched"])), "Sales pairs matched to the cent"),
        (str(len(r["exceptions"])), "Open items, each with its reason"),
        (near, "Near miss left open instead of guessed"),
        (data.money(f["cost_of_usage"]), "FIFO cost of usage, oldest layers first"),
        (f"{ready} of {v['rows']}", "Transactions ready with no decision needed"),
        (data.money(v["control_diff"]), "Dollar control difference after cleanup"),
    )
    tiles = "".join(f'<div class="pp-l-stat" data-reveal style="--d:{n * 70}ms"><span class="pp-l-stat-v" data-count data-final="{_E(val)}">{_E(val)}</span>'
                    f'<span class="pp-l-stat-l">{_E(label)}</span></div>' for n, (val, label) in enumerate(stats))
    rules = (("verified", "Amounts agree to the cent", "Close is never accepted as a match."),
             ("help", "Nothing is guessed", "Ambiguity stays open for a person to decide."),
             ("travel_explore", "Every figure traces to a source row", "Open any number and see where it came from."))
    cards = "".join(f'<div class="pp-l-rule" data-reveal style="--d:{n * 90}ms">{_icon(ico)}<div><h3>{_E(t)}</h3><p>{_E(d)}</p></div></div>'
                    for n, (ico, t, d) in enumerate(rules))
    return (
        '<section class="pp-l-band pp-l-dark pp-l-impact" id="pp-l-proof" aria-labelledby="pp-l-proof-h">'
        '<p class="pp-l-eyebrow">Controls you can check</p><h2 id="pp-l-proof-h">Built so an accountant can sign off on it</h2>'
        f'<div class="pp-l-impact-grid"><div class="pp-l-rules">{cards}</div><div class="pp-l-stats">{tiles}</div></div>'
        '<p class="pp-l-note">These figures come from the sample data used throughout this page, not from customers.</p></section>'
    )


def _formats() -> str:
    tiles = "".join(
        f'<li data-reveal style="--d:{n * 50}ms" data-kind="{k}"><span class="pp-l-box">{_icon("check" if k == "now" else "schedule" if k == "planned" else "science")}</span>'
        f'<span>{_E(label)}<small class="pp-l-tag" data-kind="{k}">{_TAG_TEXT[k]}</small></span></li>' for n, (label, k) in enumerate(_FORMATS))
    return (
        '<section class="pp-l-band pp-l-teal pp-l-dark pp-l-formats" aria-labelledby="pp-l-fmt-h">'
        '<p class="pp-l-eyebrow">What you can bring</p><h2 id="pp-l-fmt-h">Reconcile, cost, and review the data you already have</h2>'
        f'<ul class="pp-l-fmt-grid">{tiles}</ul></section>'
    )


def _nav() -> str:
    """The public bar. The wordmark is the one st.logo draws in the app header, so it is not repeated here."""
    return (
        '<nav class="pp-l-nav" aria-label="Janota FIN"><div class="pp-l-links">'
        '<button type="button" data-scroll="pp-l-solutions">Solutions</button>'
        '<button type="button" data-scroll="pp-l-how">How It Works</button>'
        '<button type="button" data-scroll="pp-l-pricing">Pricing</button>'
        '<a href="/workspace" target="_self" data-route>Workspace</a></div>'
        '<button type="button" class="pp-l-btn pp-l-btn-primary pp-l-btn-sm" data-scroll="pp-l-pricing">Explore Plans</button></nav>'
    )


def _faq_and_story() -> str:
    faqs = "".join(f'<details class="pp-l-faq"><summary>{_E(q)}</summary><p>{_E(a)}</p></details>' for q, a in data.FAQS)
    story = "".join(f"<p>{_E(p)}</p>" for p in data.BACKGROUND)
    return (
        '<section class="pp-l-section pp-l-split" id="pp-l-faq" aria-labelledby="pp-l-faq-h"><div>'
        '<p class="pp-l-eyebrow">Questions</p><h2 id="pp-l-faq-h">Before you buy</h2>'
        f'{faqs}</div><aside class="pp-l-story"><p class="pp-l-eyebrow">Where it came from</p><h2>Built by the accounting work itself</h2>'
        f'{story}<p class="pp-l-note">The Invoice Lifecycle Hub below is a prototype: its Outlook mail and sign-in are simulated.</p>'
        '<a class="pp-l-btn pp-l-btn-ghost" href="/invoice-hub" target="_self" data-route>Preview the Invoice Workflow Hub (prototype)</a></aside></section>'
    )


def _closing() -> str:
    return (
        '<section class="pp-l-close" aria-labelledby="pp-l-close-h"><h2 id="pp-l-close-h">Match the routine. Review the rest.</h2>'
        '<p>See a populated result in seconds, then choose the plan that fits your team.</p>'
        '<div class="pp-l-actions"><button type="button" class="pp-l-btn pp-l-btn-light" data-demo>Try Interactive Demo</button>'
        '<button type="button" class="pp-l-btn pp-l-btn-outline" data-scroll="pp-l-pricing">Explore Plans</button></div></section>'
    )


def _footer() -> str:
    solutions = "".join(f'<li><a href="/{_E(s.app_path)}?demo=1" target="_self">{_E(s.name)}</a></li>' for s in data.SOLUTIONS)
    return (
        '<footer class="pp-l-footer"><div class="pp-l-foot-brand"><span class="pp-wordmark" role="img" aria-label="Janota FIN"></span>'
        '<p>Accounting automation that keeps the controls an accountant expects: amounts agree to the cent, nothing is '
        'guessed, and every figure traces to a source row.</p></div>'
        f'<nav aria-label="Solutions"><h4>Solutions</h4><ul>{solutions}</ul></nav>'
        '<nav aria-label="Product"><h4>Product</h4><ul>'
        '<li><button type="button" data-scroll="pp-l-how">How it works</button></li>'
        '<li><button type="button" data-scroll="pp-l-pricing">Plans and pricing</button></li>'
        '<li><a href="/workspace" target="_self" data-route>Workspace</a></li>'
        '<li><a href="/feedback" target="_self" data-route>Reviews &amp; Feedback</a></li></ul></nav>'
        '<div class="pp-l-foot-note"><h4>Good to know</h4><p>Sign-in and checkout are simulated; nothing is charged and '
        'prices are placeholders. The Invoice Lifecycle Hub is a prototype.</p></div></footer>'
    )


@functools.lru_cache(maxsize=1)
def _wordmark_style() -> str:
    """The Janota FIN wordmark as one CSS variable, so the header logo, report previews, checkout confirmation and footer
    all show the same image without repeating its bytes in the page."""
    try:
        data_uri = base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"<style>:root{{--pp-wordmark:url(data:image/png;base64,{data_uri})}}</style>"


def _script() -> str:
    return f"<script>{(_HERE / 'landing.js').read_text(encoding='utf-8')}</script>"


# --- Streamlit sections --------------------------------------------------------------------------------------------------

def _deliverables() -> None:
    st.html('<div class="pp-land"><section class="pp-l-section pp-l-sec-head" id="pp-l-reports"><p class="pp-l-eyebrow">Deliverables</p>'
            '<h2>What you take away</h2><p class="pp-l-lede">Real files, not screenshots. Preview one here or download it '
            'as a CSV you can open in Excel. All three come from the same sample data as the examples above.</p></section></div>')
    for (key, title, kind, filename, builder, blurb), col in zip(data.REPORTS, st.columns(len(data.REPORTS), gap="medium")):
        with col, st.container(key=f"pp-l-report-{key}", border=True):
            st.html(f'<p class="pp-l-kind">{_E(kind)}</p><h3 class="pp-l-rep-h">{_E(title)}</h3><p class="pp-l-rep-p">{_E(blurb)}</p>')
            if st.button("Preview Report", key=f"pp-l-prev-{key}", width="stretch"):
                _preview_dialog(title, builder)
            st.download_button("Download Sample", data.report_csv(key), file_name=filename, mime="text/csv",
                               key=f"pp-l-dl-{key}", width="stretch", type="primary")


@st.dialog("Report preview", width="large")
def _preview_dialog(title, builder) -> None:
    header, rows = builder()
    st.html('<div class="pp-land"><span class="pp-wordmark" role="img" aria-label="Janota FIN"></span></div>')
    st.subheader(title)
    st.caption("Sample data. The downloaded file has exactly these rows.")
    st.dataframe([dict(zip(header, r)) for r in rows], hide_index=True, width="stretch")


def _stepper(step: int, tier=None, annual: bool = False) -> None:
    items = "".join(
        f'<li data-state="{"done" if n < step else "now" if n == step else "todo"}"><span>{n}</span>{_E(label)}</li>'
        for n, label in enumerate(_STEPS, 1))
    chosen = (f'<p class="pp-l-chosen">Your plan: <strong>{_E(tier.name)}</strong> &middot; '
              f'{"billed once a year" if annual else "billed monthly"}</p>') if tier is not None and step > 1 else ""
    st.html(f'<div class="pp-land"><ol class="pp-l-stepper" aria-label="Purchase steps">{items}</ol>{chosen}</div>')


def _tier(plan_id: str):
    return next((t for t in TIERS if t.id == plan_id), None)


def _restart() -> None:
    for key in (STEP, PLAN, TEMPLATE, ANNUAL, PENDING):
        st.session_state.pop(key, None)
    st.rerun()


def _step_plan() -> None:
    """Step 1: choose a plan and a billing interval. Amounts are explicit, with and without the placeholder sales tax."""
    cycle = st.segmented_control("Billing interval", [_MONTHLY, _ANNUAL], default=_MONTHLY, key=_CYCLE_KEY)
    annual = cycle == _ANNUAL
    st.session_state[ANNUAL] = annual  # the billing widget is gone once a plan is chosen; keep its answer
    for tier, col in zip(TIERS, st.columns(len(TIERS), gap="large")):
        with col, st.container(key=f"pp-plan-{tier.id}", border=True):
            total_line = ""
            if tier.monthly_price is not None:
                base, tax, total = sales_page.totals(tier, annual)
                total_line = (f'<p class="pp-l-total">Total charge {sales_page.money(total)} <small>({sales_page.money(base)} + '
                              f'{sales_page.money(tax)} placeholder tax)</small></p>')
            st.html(sales_page.tier_html(tier, annual, total_line))
            label = "Contact Sales" if tier.monthly_price is None else f"Choose {tier.name}"
            if st.button(label, key=f"pp-l-choose-{tier.id}", width="stretch", type="primary" if tier.featured else "secondary"):
                if tier.monthly_price is None:
                    st.session_state.pop(sales_page._SENT_KEY, None)
                    sales_page.contact_dialog()
                else:
                    st.session_state[PLAN] = tier.id
                    st.session_state[STEP] = 2
                    st.rerun()
    st.html(sales_page.comparison_html())


def _step_review(tier, annual: bool) -> None:
    """Step 2: what is included and the total charge."""
    apps = {a.url_path: a.title for a in APPS}
    running = [apps[i] for t in TIERS for i in t.tool_ids if TIERS.index(t) <= TIERS.index(tier)]
    included = "".join(f"<li>{_E(x)}</li>" for x in running)
    st.html(f'<div class="pp-l-review"><h3>{_E(tier.name)} plan, billed {"once a year" if annual else "monthly"}</h3>'
            f'<h4>Included applications</h4><ul>{included}</ul><h4>Also included</h4>'
            f'<ul>{"".join(sales_page._feature_li(f) for f in tier.features)}</ul></div>')
    st.html(sales_page.order_summary(tier, annual))
    back, nxt = st.columns(2)
    if back.button("Change plan", key="pp-l-back-1", width="stretch"):
        st.session_state[STEP] = 1
        st.rerun()
    if nxt.button("Continue to account", key="pp-l-next-2", type="primary", width="stretch"):
        st.session_state[STEP] = 3 if not st.session_state.get(ACCOUNT) else 4
        st.rerun()


def _finish_sign_in(account: dict) -> None:
    st.session_state[ACCOUNT] = account
    st.session_state.pop(PENDING, None)
    st.session_state[STEP] = 4
    st.rerun()


def _enroll_two_factor(pending: dict) -> None:
    """Show the new authenticator secret and require one valid code before the account is created."""
    st.markdown("**Set up two-factor authentication**")
    st.write("Add this account to an authenticator app (such as Microsoft Authenticator, Google Authenticator or "
             "1Password). The app makes codes on your device without a network, a phone number or email.")
    st.code(billing.totp_secret_display(pending["totp_secret"]), language=None)
    st.caption("Enter the key above in the app (time-based, 6 digits), or paste this address if it accepts one:")
    st.code(billing.totp_uri(pending["totp_secret"], pending["email"]), language=None)
    with st.form("pp-l-2fa", border=True, clear_on_submit=True):
        code = st.text_input("6-digit code from the app", max_chars=7, autocomplete="off")
        go = st.form_submit_button("Verify and create account", type="primary")
    if go:
        step = billing.verify_totp(pending["totp_secret"], code)
        if step is None:
            st.error("That code is not valid. Check the key in your app and the device clock, then try the next code.")
        else:
            account = {**pending, "totp_enrolled": True, "totp_last": -1}  # enrolling does not use up a code
            st.session_state.setdefault(ACCOUNTS, {})[account["username"].lower()] = account
            _finish_sign_in(account)
    if st.button("Start over", key="pp-l-2fa-cancel", type="tertiary"):
        st.session_state.pop(PENDING, None)
        st.rerun()


def _step_account() -> None:
    """Step 3: a demo account that lives in this session only. It needs a name, email, login and password, and an
    offline authenticator code (TOTP). The password is kept only as a salted hash; nothing here gates any tool."""
    st.info("Simulated account: it exists only in this browser session. The password is kept only as a salted hash "
            "for this session, and the second factor is a code from an authenticator app, generated offline. It does "
            "not control access to any tool.")
    pending = st.session_state.get(PENDING)
    if pending:
        _enroll_two_factor(pending)
        return
    create, sign_in = st.tabs(["Create account", "Sign in"])
    with create:
        with st.form("pp-l-create", border=True, clear_on_submit=True):
            c1, c2 = st.columns(2)
            first = c1.text_input("First name", autocomplete="given-name")
            last = c2.text_input("Last name", autocomplete="family-name")
            email = st.text_input("Work email", placeholder="you@company.com", autocomplete="email")
            username = st.text_input("Login name", autocomplete="username", help="4 to 32 letters, numbers, dots, dashes or underscores.")
            p1, p2 = st.columns(2)
            password = p1.text_input("Password", type="password", autocomplete="new-password", help="At least 12 characters, with letters and numbers.")
            confirm = p2.text_input("Confirm password", type="password", autocomplete="new-password")
            go = st.form_submit_button("Continue to two-factor setup", type="primary")
        if go:
            problems = []
            if not first.strip() or not last.strip():
                problems.append("Enter your first and last name.")
            if not sales_page._EMAIL_RE.match(email.strip()):
                problems.append("Enter a valid email address.")
            problems += billing.validate_login(username, password, confirm)
            if username.strip().lower() in st.session_state.get(ACCOUNTS, {}):
                problems.append("That login name is already used in this session.")
            if problems:
                for problem in problems:
                    st.error(problem)
            else:
                salt, digest = billing.hash_password(password)
                st.session_state[PENDING] = {
                    "first": first.strip(), "last": last.strip(), "name": f"{first.strip()} {last.strip()}",
                    "email": email.strip().lower(), "username": username.strip(), "salt": salt, "hash": digest,
                    "totp_secret": billing.new_totp_secret(),
                }
                st.rerun()
    with sign_in:
        with st.form("pp-l-signin", border=True, clear_on_submit=True):
            login = st.text_input("Login name", key="pp-l-signin-login", autocomplete="username")
            pw = st.text_input("Password", type="password", key="pp-l-signin-pw", autocomplete="current-password")
            otp = st.text_input("6-digit code from your authenticator app", key="pp-l-signin-otp", max_chars=7, autocomplete="off")
            go = st.form_submit_button("Sign in", type="primary")
        if go:
            account = st.session_state.get(ACCOUNTS, {}).get(login.strip().lower())
            step = None
            if account and billing.verify_password(pw, account["salt"], account["hash"]):
                step = billing.verify_totp(account["totp_secret"], otp, last_used=account.get("totp_last", -1))
            if step is None:  # one message for every failure, so it does not reveal which part was wrong
                st.error("The login name, password or code is not right, or no demo account exists in this session. "
                         "Create one on the other tab.")
            else:
                account["totp_last"] = step
                _finish_sign_in(account)
    if st.button("Back", key="pp-l-back-3"):
        st.session_state[STEP] = 2
        st.rerun()


def _step_checkout(tier, annual: bool) -> None:
    """Step 4: the clearly labeled simulated checkout."""
    st.html('<p class="pp-l-sim">SIMULATED CHECKOUT &middot; Nothing is charged</p>')
    st.html(sales_page.order_summary(tier, annual))
    if sales_page.payment_section(tier, annual, key="pp-l-pay", quiet=True, account=st.session_state.get(ACCOUNT)):
        st.session_state[STEP] = 5
        st.rerun()
    if st.button("Back", key="pp-l-back-4"):
        st.session_state[STEP] = 2
        st.rerun()


def _step_confirm(tier) -> None:
    """Step 5: confirmation, with the way into the first workflow."""
    account = st.session_state.get(ACCOUNT) or {}
    order = (st.session_state.get(sales_page.REGISTER_KEY) or [{}])[-1]
    st.html(f'<div class="pp-l-confirm"><span class="pp-wordmark" role="img" aria-label="Janota FIN"></span><h3>Demo order recorded</h3>'
            f'<p>Thanks, {_E(account.get("first", "there"))}. Your {_E(tier.name)} plan ({_E(order.get("cycle", ""))}) is set up '
            f'in this demo, billed {"once a year" if order.get("cycle") == "Annual" else "monthly"}. Total {sales_page.money(order.get("total", 0))}: '
            f'<strong>not charged</strong>.</p>'
            f'<p class="pp-l-note">A confirmation for {_E(account.get("email", ""))} would be sent here; email delivery is not connected.</p></div>')
    if st.button("Set Up Your First Workflow", key="pp-l-setup", type="primary"):
        st.session_state[STEP] = 6
        st.rerun()


# What each template expects, and the sample file's columns a person would line them up with.
_MAPPING = {
    "recon": (("QuickBooks PO", "PO"), ("QuickBooks Amount", "Amount"), ("Infinium PO", "PO"), ("Infinium Amount", "Amount")),
    "fifo": (("Material", "Item"), ("Period", "Period"), ("Quantity", "Qty"), ("Value", "Amount")),
    "review": (("Vendor text", "Vendor"), ("Amount", "Amount"), ("Date", "Date")),
}
_SAMPLE_COLUMNS = ("Item", "Vendor", "PO", "Invoice", "Date", "Period", "Qty", "Amount")


def _step_onboarding(tier) -> None:
    """Step 6: choose a template, line up the fields, then enter the workspace."""
    st.html('<div class="pp-l-review"><h3>Set up your first workflow</h3><p>Choose a template, check how its fields line up '
            'with your data, then open the workspace. Your own file is mapped when you upload it.</p></div>')
    names = {s.id: s.name for s in data.SOLUTIONS}
    choice = st.radio("Template", list(names), format_func=names.get, key=TEMPLATE, horizontal=True)
    st.markdown("**Map data**")
    cols = st.columns(2)
    complete = True
    for n, (field, default) in enumerate(_MAPPING[choice]):
        with cols[n % 2]:
            picked = st.selectbox(field, ("(not mapped)", *_SAMPLE_COLUMNS), index=_SAMPLE_COLUMNS.index(default) + 1,
                                  key=f"pp-l-map-{choice}-{n}")
            complete = complete and picked != "(not mapped)"
    if complete:
        st.success("Every field is mapped.")
    else:
        st.warning("Map every field to continue.")
    st.caption("Column mapping is confirmed again inside the tool when you upload your file. The workspace opens with sample "
               "data loaded so you can see the result first.")
    if st.button("Enter the workspace", key="pp-l-enter", type="primary", disabled=not complete):
        target = next(a for a in APPS if a.url_path == data.SOLUTION_BY_ID[choice].app_path)
        st.switch_page(target.script, query_params={"demo": "1"})  # ?demo=1 loads the tool's sample data
    if st.button("Go to the Dashboard instead", key="pp-l-dash"):
        st.switch_page(DASHBOARD_SCRIPT)


def _scroll_to_requested_section() -> None:
    """Back to solutions (the demo banner) arrives as ?to=solutions: scroll the explorer into view once, then drop the parameter."""
    if st.query_params.get(SECTION_PARAM) == SOLUTIONS_SECTION:
        del st.query_params[SECTION_PARAM]
        st.html("<script>/* to solutions */ setTimeout(function () { var el = document.getElementById('pp-l-solutions'); "
                "if (el) el.scrollIntoView({ block: 'start' }); }, 600);</script>", unsafe_allow_javascript=True)


def _scroll_on_step_change(step: int) -> None:
    """Bring the purchase section back into view when the visitor moves to another step (not on first load)."""
    seen = st.session_state.get("pp_l_seen_step")
    st.session_state["pp_l_seen_step"] = step
    if seen is not None and seen != step:
        # The step number in the text makes this a new script each time, so it runs once per step.
        st.html(f"<script>/* step {step} */ setTimeout(function () {{ var el = document.getElementById('pp-l-pricing'); "
                f"if (el) el.scrollIntoView({{ block: 'start' }}); }}, 250);</script>", unsafe_allow_javascript=True)


def _purchase() -> None:
    st.html('<div class="pp-land"><section class="pp-l-section pp-l-sec-head" id="pp-l-pricing"><p class="pp-l-eyebrow">Choose a plan</p>'
            '<h2>Plans and pricing</h2><p class="pp-l-lede">Explicit prices, billed monthly or once a year (two months free). '
            'The checkout that follows is a simulation: nothing is charged. Prices are placeholders.</p></section></div>')
    step = st.session_state.get(STEP, 1)
    tier = _tier(st.session_state.get(PLAN))
    if step > 1 and tier is None:
        step = 1
    annual = bool(st.session_state.get(ANNUAL))
    _stepper(step, tier, annual)
    _scroll_on_step_change(step)
    if step == 1:
        _step_plan()
        return
    if step == 2:
        _step_review(tier, annual)
    elif step == 3:
        _step_account()
    elif step == 4:
        _step_checkout(tier, annual)
    elif step == 5:
        _step_confirm(tier)
    else:
        _step_onboarding(tier)
    if step < 6 and st.button("Start over", key="pp-l-restart", type="tertiary"):
        _restart()


# --- page ---------------------------------------------------------------------------------------------------------------------

st.html(f"{style_tag('landing')}{style_tag('sales-page')}{_wordmark_style()}"
        f'<div class="pp-land" id="pp-l-top">{_nav()}{_hero()}{_marquee()}{_explorer()}{_walkthrough()}{_impact()}{_formats()}</div>{_script()}',
        unsafe_allow_javascript=True)
_scroll_to_requested_section()
_deliverables()
_purchase()
st.html(f'<div class="pp-land">{_faq_and_story()}{_closing()}{_footer()}</div>')
