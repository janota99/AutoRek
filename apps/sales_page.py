"""Sales page: three purchase tiers for the suite. Not a registered page.

It is reachable only from the Dashboard: ``apps/dashboard.py`` draws it in place of the cards when
the "View pricing & plans" button has set ``st.session_state["pp_view"] = "pricing"``. It has no
URL and no navigation-bar entry; ``app.py`` clears the flag whenever the visitor leaves the Dashboard.

Checkout is not connected to a payment processor. Choosing a plan shows an order summary and
charges nothing. The prices below are placeholders: change them here, in one place.
"""
from __future__ import annotations

import html
from dataclasses import dataclass

import streamlit as st

VIEW_KEY = "pp_view"          # session_state: "pricing" while the sales page is showing
PRICING_VIEW = "pricing"
PLAN_KEY = "pp_plan"         # session_state: id of the plan the visitor chose
_CYCLE_KEY = "pp_cycle"       # session_state: billing selector
_MONTHLY, _ANNUAL = "Monthly", "Annual (2 months free)"
_ANNUAL_MONTHS_BILLED = 10    # twelve months for the price of ten


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


TIERS: tuple[Tier, ...] = (
    Tier(
        id="starter", name="Starter", positioning="Basic",
        audience="Small businesses and single-focus accountants",
        monthly_price=49,
        features=(
            "Sales Tax Review: tax classification and vendor reconciliation",
            "Standard CSV and Excel file uploads",
            "Basic reporting and email support",
        ),
    ),
    Tier(
        id="professional", name="Professional", positioning="Most popular",
        audience="Growing businesses and mid-sized accounting teams",
        monthly_price=149, inherits="Everything in Starter",
        features=(
            "FIFO Inventory: inventory costs, control reviews, and fiscal period closes",
            "Sales Reconciliation: QuickBooks and Infinium sales matching, with exception reviews",
            "Multi-user workspace access and session history",
        ),
        featured=True,
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


def _order_summary(tier: Tier, annual: bool) -> str:
    if tier.monthly_price is None:
        body = ("<strong>Enterprise</strong> is quoted to your organization. Our team will scope the "
                "integrations, user roles, and support before agreeing a price.")
    else:
        headline, unit = _price(tier, annual)
        body = (f"<strong>{html.escape(tier.name)}</strong>, billed {'annually' if annual else 'monthly'}: "
                f"<strong>{headline}</strong> {html.escape(unit)}.")
    return (f'<div class="pp-order">{body}<br>Online checkout is not connected yet, so nothing has been '
            "charged and no order has been placed.</div>")


def render() -> None:
    """Draw the sales page. Call only from the Dashboard."""
    st.html(
        f"<style>{_CSS}</style>"
        '<div class="pp-sales"><p class="pp-eyebrow">Panhandle Pure</p>'
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

    chosen = next((t for t in TIERS if t.id == st.session_state.get(PLAN_KEY)), None)
    if chosen:
        st.html(_order_summary(chosen, annual))
