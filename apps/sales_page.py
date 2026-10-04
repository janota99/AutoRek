"""Sales page: three purchase tiers for the suite. Not a registered page.

It is reachable only from the Dashboard: ``apps/dashboard.py`` draws it in place of the cards when
the "View pricing & plans" button has set ``st.session_state["pp_view"] = "pricing"``. It has no
URL and no navigation-bar entry; ``app.py`` clears the flag whenever the visitor leaves the Dashboard.

Checkout is not connected to a payment processor. Choosing a plan shows an order summary and
charges nothing. The prices below are placeholders: change them here, in one place.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

import json

import streamlit as st

from shared.layout import APPS

VIEW_KEY = "pp_view"          # session_state: "pricing" while the sales page is showing
PRICING_VIEW = "pricing"
PLAN_KEY = "pp_plan"         # session_state: id of the plan the visitor chose
_CYCLE_KEY = "pp_cycle"       # session_state: billing selector
_MONTHLY, _ANNUAL = "Monthly", "Annual (2 months free)"
_ANNUAL_MONTHS_BILLED = 10    # twelve months for the price of ten
TAX_RATE = Decimal("0.0825")  # placeholder sales tax rate shown in the order summary
_SENT_KEY = "pp_contact_sent"  # session_state: contact form submitted (stub)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class Tier:
    id: str
    name: str
    audience: str
    positioning: str                 # Basic / Core / Premium label shown above the name
    monthly_price: int | None        # whole US dollars; None = custom quote
    features: tuple[str, ...]
    inherits: str = ""               # "Everything in Starter", shown as the first line
    featured: bool = False
    action: str = "Choose plan"
    tool_ids: tuple[str, ...] = ()   # the applications this tier adds (shared.layout.APPS url_paths)


TIERS: tuple[Tier, ...] = (
    Tier(
        id="starter", name="Starter", positioning="Basic",
        audience="Small businesses and single-focus accountants",
        monthly_price=20,
        features=(
            "Sales Tax Review: tax classification and vendor reconciliation",
            "Standard CSV and Excel file uploads",
            "Basic reporting and email support",
        ),
        tool_ids=("sales-tax",),
    ),
    Tier(
        id="professional", name="Professional", positioning="Most popular",
        audience="Growing businesses and mid-sized accounting teams",
        monthly_price=100, inherits="Everything in Starter",
        features=(
            "FIFO Inventory: inventory costs, control reviews, and fiscal period closes",
            "Sales Reconciliation: QuickBooks and Infinium sales matching, with exception reviews",
            "Multi-user workspace access and session history",
        ),
        featured=True,
        tool_ids=("fifo-inventory", "recon"),
    ),
    Tier(
        id="enterprise", name="Enterprise", positioning="Premium",
        audience="Large organizations that need end-to-end automation",
        monthly_price=None, inherits="Everything in Professional",
        features=(
            "Invoice Lifecycle Hub: full tracking from Outlook through payment, with an analytics dashboard",
            "Priority API integrations, such as custom QuickBooks or ERP sync",
            "Advanced audit logs, custom user roles, and dedicated support",
        ),
        action="Contact sales",
        tool_ids=("invoice-hub",),
    ),
)

_CSS = """
.stApp:has(.pp-sales) { background: radial-gradient(1200px 500px at 50% -10%, #e4edf8 0%, #f0f4f8 60%); }
.stApp:has(.pp-sales) .block-container { max-width: 1100px; margin-left: auto; margin-right: auto; }
.pp-sales { font-family: Inter, "Segoe UI", system-ui, sans-serif; color: #475569; }
.pp-sales .pp-eyebrow { margin: 0 0 .5rem; color: #4b81b8; font-size: .875rem; font-weight: 700;
    letter-spacing: .14em; text-transform: uppercase; }
.pp-sales h1 { margin: 0 0 .6rem; padding: 0; color: #153d68; font-size: 2.5rem; font-weight: 800;
    letter-spacing: -.02em; line-height: 1.15; }
.pp-sales p.pp-lede { margin: 0; color: #64748b; font-size: .95rem; }
[class*="st-key-pp-plan-"] { background: #fff; border-radius: .9rem; border: 1px solid #e2e8f0;
    box-shadow: 0 1px 3px rgba(15, 35, 65, .08); }
.st-key-pp-plan-professional { border: 2px solid #2f70a8; box-shadow: 0 8px 24px rgba(47, 112, 168, .18); }
.pp-tier-label { margin: 0; color: #4b81b8; font-size: .75rem; font-weight: 700;
    letter-spacing: .12em; text-transform: uppercase; }
.pp-tier-name { margin: .25rem 0 .25rem; color: #153d68; font-size: 1.5rem; font-weight: 800; }
.pp-tier-for { margin: 0 0 1rem; min-height: 2.8em; color: #64748b; font-size: .9rem; line-height: 1.4; }
.pp-price { margin: 0 0 1rem; color: #153d68; font-size: 2.4rem; font-weight: 800; line-height: 1; }
.pp-price small { color: #64748b; font-size: .9rem; font-weight: 500; }
.pp-feats { margin: 0 0 .5rem; padding: 0; list-style: none; }
[data-testid="stColumn"]:has([class*="st-key-pp-plan-"]) > [data-testid="stVerticalBlock"] { height: 100%; }
[class*="st-key-pp-plan-"] { height: 100%; }
[class*="st-key-pp-plan-"] > [data-testid="stVerticalBlock"] { height: 100%; justify-content: space-between; }
.pp-feats li { position: relative; margin: 0 0 .7rem; padding-left: 1.5rem; font-size: .92rem; line-height: 1.45; }
.pp-feats li::before { content: "\\2713"; position: absolute; left: 0; color: #2f70a8; font-weight: 800; }
.pp-feats li.pp-inherit { color: #153d68; font-weight: 700; }
.pp-order { margin: 1.5rem 0 0; padding: 1rem 1.2rem; border-left: 3px solid #2f70a8; border-radius: .2rem .5rem .5rem .2rem;
    background: #eaf1f7; color: #3b5266; font-size: .95rem; line-height: 1.55; }
.pp-order strong { color: #153d68; }
.pp-order { animation: pp-fade .3s ease; }
@keyframes pp-fade { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
.pp-lines { width: 100%; margin: 0 0 .8rem; border-collapse: collapse; }
.pp-lines td { padding: .3rem 0; border: 0; }
.pp-lines td:last-child { text-align: right; font-variant-numeric: tabular-nums; }
.pp-lines tr.pp-total td { border-top: 1px solid #b9cadb; color: #153d68; font-weight: 800; font-size: 1.05rem; }
[class*="st-key-pp-plan-"] { transition: transform .25s ease, box-shadow .25s ease, border-color .25s ease; }
[class*="st-key-pp-plan-"]:hover { transform: translateY(-6px); box-shadow: 0 12px 24px rgba(0, 0, 0, .1);
    border-color: #2563eb; }
.pp-compare { display: flex; flex-direction: column; align-items: flex-start; gap: .35rem; min-height: 3.4rem;
    margin: 1rem 0 0; padding: .7rem 1rem; border: 1px dashed #b9cadb; border-radius: .6rem;
    background: rgba(255, 255, 255, .6); color: #3b5266; font-size: .97rem; line-height: 1.45;
    transition: background .2s ease, border-color .2s ease; }
.pp-compare-active { border-style: solid; border-color: #2f70a8; background: #fff; }
.pp-compare-name { color: #153d68; font-size: 1.05rem; }
.pp-compare-group b { margin-right: .35rem; color: #2f70a8; font-weight: 700; }
.pp-plan-hover { border-color: #2563eb; }
"""


def _price(tier: Tier, annual: bool) -> tuple[str, str]:
    """(headline price, unit text) for the chosen billing cycle."""
    if tier.monthly_price is None:
        return "Custom", ""
    if annual:
        return f"${tier.monthly_price * _ANNUAL_MONTHS_BILLED:,}", "per year"
    return f"${tier.monthly_price:,}", "per month"


def _tier_html(tier: Tier, annual: bool) -> str:
    headline, unit = _price(tier, annual)
    items = ([f'<li class="pp-inherit">{html.escape(tier.inherits)}</li>'] if tier.inherits else [])
    items += [f"<li>{html.escape(f)}</li>" for f in tier.features]
    return (
        f'<p class="pp-tier-label">{html.escape(tier.positioning)}</p>'
        f'<h3 class="pp-tier-name">{html.escape(tier.name)}</h3>'
        f'<p class="pp-tier-for">{html.escape(tier.audience)}</p>'
        f'<p class="pp-price">{headline} {f"<small>{html.escape(unit)}</small>" if unit else ""}</p>'
        f'<ul class="pp-feats">{"".join(items)}</ul>'
    )


_HOVER_JS = r"""
(function () {
  if (window.__ppPlanHover) return;           // Streamlit re-runs this block; bind the document once
  window.__ppPlanHover = true;
  var CARD = '[class*="st-key-pp-plan-"]';
  function bar() { return document.querySelector(".pp-compare"); }
  function plans() {
    var el = bar();
    try { return el ? JSON.parse(el.dataset.plans) : []; } catch (e) { return []; }
  }
  function money(n) { return "$" + n.toLocaleString("en-US"); }
  function line(label, text) {
    var span = document.createElement("span");
    span.className = "pp-compare-group";
    var b = document.createElement("b");
    b.textContent = label;
    span.appendChild(b);
    span.appendChild(document.createTextNode(" " + text));
    return span;
  }
  function show(card) {
    var el = bar();
    if (!el) return;
    var id = (card.className.match(/st-key-pp-plan-([a-z]+)/) || [])[1];
    var plan = plans().filter(function (p) { return p.id === id; })[0];
    if (!plan) return;
    el.textContent = "";
    var name = line("Plan Name:", plan.name);
    name.classList.add("pp-compare-name");
    el.appendChild(name);
    if (plan.monthly === null) {
      el.appendChild(line("Price:", "Quoted to your organization"));
    } else {
      var saved = plan.monthly * 12 - plan.annual;
      el.appendChild(line("Price:", money(plan.monthly) + " per month, or " + money(plan.annual) +
        " per year (saves " + money(saved) + ")"));
    }
    var count = plan.tools.length;
    el.appendChild(line("Adds:", count + (count === 1 ? " application: " : " applications: ") + plan.tools.join(", ")));
    if (plan.inherits) el.appendChild(line("Includes:", plan.inherits.replace("Everything in ", "all of ")));
    el.classList.add("pp-compare-active");
    card.classList.add("pp-plan-hover");
  }
  function reset(card) {
    var el = bar();
    card.classList.remove("pp-plan-hover");
    if (!el) return;
    el.textContent = el.dataset.hint;
    el.classList.remove("pp-compare-active");
  }
  // mouseenter and mouseleave do not bubble, so listen in the capture phase and act only on the card itself.
  document.addEventListener("mouseenter", function (ev) {
    if (ev.target.matches && ev.target.matches(CARD)) show(ev.target);
  }, true);
  document.addEventListener("mouseleave", function (ev) {
    if (ev.target.matches && ev.target.matches(CARD)) reset(ev.target);
  }, true);
})();
"""


def _compare_bar() -> str:
    """The bar a hovered plan card fills in. Plans travel as JSON (strings, numbers, booleans, null),
    the same shape as the `plans` collection in mongodb/plans.json."""
    titles = {app.url_path: app.title for app in APPS}
    plans = [{
        "id": t.id, "name": t.name, "monthly": t.monthly_price,
        "annual": None if t.monthly_price is None else t.monthly_price * _ANNUAL_MONTHS_BILLED,
        "tools": [titles[i] for i in t.tool_ids], "inherits": t.inherits or None, "featured": t.featured,
    } for t in TIERS]
    hint = "Hover over a plan to see its price and the applications it adds."
    return (f'<div class="pp-compare" role="status" aria-live="polite" data-hint="{hint}" '
            f'data-plans="{html.escape(json.dumps(plans), quote=True)}">{hint}</div>')


def _money(cents: int) -> str:
    return f"${cents // 100:,}.{cents % 100:02d}"


def _totals(tier: Tier, annual: bool) -> tuple[int, int, int]:
    """(base, tax, total) in cents. Tax is rounded half-up to the penny."""
    base = tier.monthly_price * 100 * (_ANNUAL_MONTHS_BILLED if annual else 1)
    tax = int((Decimal(base) * TAX_RATE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return base, tax, base + tax


def _order_summary(tier: Tier, annual: bool) -> str:
    note = ("Online checkout is not connected yet, so nothing has been charged and no order "
            "has been placed.")
    if tier.monthly_price is None:
        return ('<div class="pp-order"><strong>Enterprise</strong> is quoted to your organization. Our '
                "team will scope the integrations, user roles, and support before agreeing a price."
                f"<br>{note}</div>")
    base, tax, total = _totals(tier, annual)
    cycle = "year" if annual else "month"
    rows = (f"<tr><td>{html.escape(tier.name)} base price (per {cycle})</td><td>{_money(base)}</td></tr>"
            f"<tr><td>Sales tax ({TAX_RATE * 100:.2f}%)</td><td>{_money(tax)}</td></tr>"
            f'<tr class="pp-total"><td>Total</td><td>{_money(total)}</td></tr>')
    # Keyed by plan and cycle so the browser re-runs the fade-in each time the total changes.
    return (f'<div class="pp-order" data-k="{tier.id}-{cycle}"><table class="pp-lines">{rows}</table>'
            f"{note}</div>")


@st.dialog("Contact sales")
def _contact_dialog() -> None:
    """Enterprise enquiry form. Submission is a stub: nothing is sent or stored."""
    if st.session_state.get(_SENT_KEY):
        st.success("Thanks. Your inquiry has been noted. Inquiries are responded to within 2-3 business days.")
        st.caption("Contact delivery is not connected yet, so no message was actually sent.")
        if st.button("Close", key="pp-contact-close"):
            st.session_state.pop(_SENT_KEY, None)
            st.rerun()
        return
    with st.form("pp-contact-form", border=False):
        email = st.text_input("Email (required)", placeholder="you@company.com")
        message = st.text_area("Message", placeholder="Tell us about your organization and needs.")
        st.caption("Inquiries are responded to within 2-3 business days.")
        submitted = st.form_submit_button("Submit", type="primary")
    if submitted:
        if not _EMAIL_RE.match(email.strip()):
            st.error("Enter a valid email address.")
        else:
            st.session_state[_SENT_KEY] = True
            st.rerun(scope="fragment")


def render() -> None:
    """Draw the sales page. Call only from the Dashboard."""
    st.html(
        f"<style>{_CSS}</style>"
        '<div class="pp-sales"><p class="pp-eyebrow">Janota Fin Automatations</p>'
        "<h1>Plans &amp; Pricing</h1>"
        '<p class="pp-lede">Pick the tier that fits your accounting team. Every plan includes the '
        "applications of the tiers below it.</p></div>"
    )
    if st.button("Back to Dashboard", icon=":material/arrow_back:", key="pp-back"):
        st.session_state.pop(VIEW_KEY, None)
        st.session_state.pop(PLAN_KEY, None)
        st.rerun()

    cycle = st.segmented_control("Billing", [_MONTHLY, _ANNUAL], default=_MONTHLY, key=_CYCLE_KEY)
    annual = cycle == _ANNUAL

    for tier, col in zip(TIERS, st.columns(len(TIERS), gap="large")):
        with col, st.container(key=f"pp-plan-{tier.id}", border=True):
            st.html(_tier_html(tier, annual))
            if st.button(tier.action, key=f"pp-choose-{tier.id}", width="stretch",
                         type="primary" if tier.featured else "secondary"):
                st.session_state[PLAN_KEY] = tier.id
                if tier.monthly_price is None:
                    st.session_state.pop(_SENT_KEY, None)
                    _contact_dialog()

    # Under the cards, not above them, so the hover text never crowds the choices.
    st.html(f"{_compare_bar()}<script>{_HOVER_JS}</script>", unsafe_allow_javascript=True)

    chosen = next((t for t in TIERS if t.id == st.session_state.get(PLAN_KEY)), None)
    if chosen:
        st.html(_order_summary(chosen, annual))
