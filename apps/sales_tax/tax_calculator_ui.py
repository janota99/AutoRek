"""The Sales Tax Calculator screen: inputs on the left, the tax summary card on the right.

Presentation only. Every number comes from ``tax_calculator`` (``Decimal``, cent-rounded); this
module formats them. Styles are the ``stc-*`` classes in shared/styles/pages/sales-tax.css.
Custom locations live in session state for the current visit only.
"""
from __future__ import annotations

import html
from datetime import datetime
from decimal import Decimal, InvalidOperation

import streamlit as st

from . import ui
from .tax_calculator import (
    LOCAL_CAP, PRESET_LOCATIONS, SAAS_TAXABLE_SHARE, STATE_RATE, Location, TaxResult, calculate, compare, to_cents,
)
from .tax_tape import tape_pdf, tape_png

_E = html.escape
_CUSTOM_KEY = "stc_custom_locations"
_SAAS_LABELS = {False: "Standard sale", True: "SaaS (80% taxable)"}


def _usd(v: Decimal) -> str:
    return f"${v:,.2f}"


def _pct(v: Decimal, places: int = 2) -> str:
    return f"{v:.{places}f}%"


def _signed(v: Decimal) -> str:
    return "±$0.00" if v == 0 else f"{'+' if v > 0 else '−'}${abs(v):,.2f}"


def _locations() -> list[Location]:
    return list(PRESET_LOCATIONS) + [Location(**d) for d in st.session_state.get(_CUSTOM_KEY, [])]


def _summary_html(r: TaxResult) -> str:
    loc = r.location
    taxable_note = (f"SaaS / data processing: {SAAS_TAXABLE_SHARE:.0%} of the price is taxable (34 TAC §3.330)"
                    if r.saas else "Standard sale: the full price is taxable")
    local_rows = "".join(
        f'<div class="stc-line"><span>{_E(ln.label)} <b class="stc-chip">{_pct(ln.effective_rate, 3)}</b></span>'
        f'<span class="stc-loc">{_usd(ln.amount)}</span></div>' for ln in r.local_lines
    ) or '<div class="stc-line"><span>No local tax</span><span class="stc-loc">$0.00</span></div>'
    badge = '<span class="stc-badge" data-saas="1">80% SaaS</span>' if r.saas else '<span class="stc-badge">100% taxable</span>'
    return f"""
<div class="stc-card">
  <div class="stc-head">
    <span class="stc-ico">{ui.icon("receipt_long")}</span>
    <div><div class="stc-title">Sales Tax Determination Summary</div>
    <div class="stc-sub">Forward calculation · Texas · {_E(loc.name)}</div></div>
  </div>
  <div class="stc-body">
    <div class="stc-row stc-muted"><span>Total gross (pre-tax)</span><span class="stc-num">{_usd(r.price)}</span></div>
    <div class="stc-pillrow" data-tone="state"><span>TX State tax ({_pct(STATE_RATE)})</span><span class="stc-num">{_usd(r.state.amount)}</span></div>
    <div class="stc-pillrow" data-tone="local"><span>Local tax ({_pct(loc.local_rate)})</span><span class="stc-num">{_usd(r.local_total)}</span></div>
    <div class="stc-pillrow" data-tone="total"><span>Total tax</span><span class="stc-num">{_usd(r.total_tax)}</span></div>
    <div class="stc-due"><span>TOTAL AMOUNT DUE (INCLUDING TAX)</span><b>{_usd(r.total_due)}</b></div>
  </div>
  <div class="stc-break">
    <div class="stc-kicker">Transaction breakdown</div>
    <div class="stc-tx">
      <div class="stc-tx-head"><span><i class="stc-dot"></i>Transaction 1</span>{badge}</div>
      <div class="stc-tx-note">{_E(taxable_note)} · {_E(loc.note or "Custom location")}</div>
      <div class="stc-line stc-muted"><span>Subtotal</span><span class="stc-num">{_usd(r.price)}</span></div>
      <div class="stc-line stc-strong"><span>Taxable amount</span><span class="stc-num">{_usd(r.taxable_amount)}</span></div>
      <div class="stc-sep"></div>
      <div class="stc-line"><span>TX State <b class="stc-chip" data-tone="state">{_pct(r.state.effective_rate, 3)}</b></span><span class="stc-state">{_usd(r.state.amount)}</span></div>
      {local_rows}
      <div class="stc-sep"></div>
      <div class="stc-line stc-strong"><span>Total tax</span><span class="stc-num">{_usd(r.total_tax)}</span></div>
      <div class="stc-total"><span>TOTAL <b class="stc-chip" data-tone="total">{_pct(r.effective_rate, 3)}</b></span><span>{_usd(r.total_due)}</span></div>
    </div>
  </div>
</div>"""


def _compare_html(price: Decimal, current: TaxResult, locations: list[Location]) -> str:
    rows = ""
    for loc, plain, saas in compare(price, locations):
        def cell(res: TaxResult) -> str:
            active = " data-active='1'" if (loc == current.location and res.saas == current.saas) else ""
            delta = res.total_due - current.total_due
            tail = "" if (loc == current.location and res.saas == current.saas) else f'<small>{_signed(delta)}</small>'
            return f'<td{active}><b>{_usd(res.total_due)}</b><span>tax {_usd(res.total_tax)}</span>{tail}</td>'
        rows += f'<tr><th>{_E(loc.name)}<small>{_pct(loc.combined_rate)} combined</small></th>{cell(plain)}{cell(saas)}</tr>'
    return (f'<table class="stc-cmp"><thead><tr><th>Location</th><th>Standard sale</th><th>SaaS (80%)</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')


def _parse_rate(text: str) -> Decimal | None:
    try:
        v = Decimal(text.strip().rstrip("%") or "0")
    except InvalidOperation:
        return None
    return v if Decimal("0") <= v <= Decimal("10") else None


def _custom_location_form() -> None:
    with st.expander("Add a custom location"):
        st.caption("For a business outside city limits, leave City at 0 and enter only the county (and any "
                   "special district) rate. Texas caps all local taxes combined at 2.00%.")
        with st.form("stc_custom_form", clear_on_submit=True):
            name = st.text_input("Location name", placeholder="e.g. Randall County, outside Amarillo")
            c1, c2, c3 = st.columns(3)
            city = c1.text_input("City %", value="0.00")
            county = c2.text_input("County %", value="0.50")
            district = c3.text_input("District %", value="0.00")
            outside = st.checkbox("Outside city limits (no city tax)", value=True)
            if st.form_submit_button("Add location", type="primary"):
                rates = [_parse_rate(t) for t in (city, county, district)]
                if not name.strip():
                    st.error("Give the location a name.")
                elif any(r is None for r in rates):
                    st.error("Rates must be numbers between 0 and 10.")
                else:
                    city_r, county_r, district_r = rates
                    if outside:
                        city_r = Decimal("0")
                    loc = Location(name.strip(), city_r, county_r, district_r,
                                   note="Custom location: out of city limits, State and county tax only." if outside
                                   else "Custom location.")
                    if loc.over_cap:
                        st.error(f"Local tax of {_pct(loc.local_rate)} is over the {_pct(LOCAL_CAP)} Texas cap. "
                                 "Check the rates.")
                    elif loc.name in {x.name for x in _locations()}:
                        st.error("A location with that name already exists.")
                    else:
                        st.session_state.setdefault(_CUSTOM_KEY, []).append({
                            "name": loc.name, "city_rate": city_r, "county_rate": county_r,
                            "district_rate": district_r, "note": loc.note})
                        # The selectbox below already exists in this run, so its key can't be set now;
                        # render_tax_calculator applies this request before the selectbox is created.
                        st.session_state["_stc_pending_location"] = loc.name
                        st.rerun()


def render_tax_calculator() -> None:
    ui.section("Sales Tax Calculator",
               "Enter a pre-tax price, pick a location, and see how much goes to the State of Texas and how much "
               "stays local. Switch location or SaaS and the amount due updates.")
    st.sidebar.caption("The calculator is standalone. It does not read the uploaded files or change any cleanup result.")

    locations = _locations()
    names = [loc.name for loc in locations]
    if "_stc_pending_location" in st.session_state:
        st.session_state["stc_location"] = st.session_state.pop("_stc_pending_location")
    if st.session_state.get("stc_location") not in names:
        st.session_state["stc_location"] = names[0]

    left, right = st.columns([5, 6], gap="large")
    with left:
        with st.container(border=True, key="st-card-calc"):
            price_in = st.number_input("Price (pre-tax)", min_value=0.0, max_value=1_000_000_000.0, value=500.0,
                                       step=10.0, format="%.2f")
            loc_name = st.selectbox("Location", names, key="stc_location")
            saas = st.segmented_control("Type of sale", list(_SAAS_LABELS.values()), default=_SAAS_LABELS[False],
                                        key="stc_saas") == _SAAS_LABELS[True]
            location = locations[names.index(loc_name)]
            st.caption(location.note or "Custom location.")
            if location.over_cap:
                st.warning("This location's local rate is over the 2.00% Texas cap.")
        _custom_location_form()

    price = to_cents(price_in)
    result = calculate(price, location, saas)
    with right:
        st.html(_summary_html(result))
        stamp = datetime.now().strftime("%b %d, %Y %I:%M %p")
        st.caption("Save a compact tape of this result to attach as backup to an invoice that needs a tax correction.")
        b1, b2 = st.columns(2)
        slug = f"sales-tax-tape-{datetime.now():%Y%m%d-%H%M%S}"
        b1.download_button("Save tape as image", tape_png(result, stamp), f"{slug}.png", "image/png",
                           icon=":material/content_cut:", use_container_width=True)
        b2.download_button("Save tape as PDF", tape_pdf(result, stamp), f"{slug}.pdf", "application/pdf",
                           icon=":material/picture_as_pdf:", use_container_width=True)

    ui.section("Compare scenarios", "Total amount due at this price for every location, as a standard sale and as "
                                    "SaaS. Small figures show the difference from the scenario selected above.")
    st.html(_compare_html(price, result, locations))
    st.caption(f"State rate {_pct(STATE_RATE)}; local tax capped at {_pct(LOCAL_CAP)} (8.25% combined maximum). "
               "Each tax line is rounded to the cent. Built-in location rates are examples: confirm your own "
               "jurisdiction rates with the Texas Comptroller before relying on them.")
