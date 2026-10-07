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
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import json

import streamlit as st

from shared.billing import digits, validate_card, validate_gift_card
from shared.layout import APPS
from shared.styles import style_tag

VIEW_KEY = "pp_view"          # session_state: "pricing" while the sales page is showing
PRICING_VIEW = "pricing"
PLAN_KEY = "pp_plan"         # session_state: id of the plan the visitor chose
_CYCLE_KEY = "pp_cycle"       # session_state: billing selector
_MONTHLY, _ANNUAL = "Monthly", "Annual (2 months free)"
_ANNUAL_MONTHS_BILLED = 10    # twelve months for the price of ten
TAX_RATE = Decimal("0.0825")  # placeholder sales tax rate shown in the order summary
TBC = "To be confirmed"
REGISTER_KEY = "pp_register"   # session_state: demo charge register (this session only; never card numbers)
_METHODS = ("Credit / debit card", "Gift card", "PayPal")
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
    # Comparison table rows. TBC means the business has not decided it yet; nothing in the app enforces a plan limit.
    users: str = TBC
    organizations: str = TBC
    processing: str = TBC
    storage: str = "Not available yet"
    support: str = TBC


TIERS: tuple[Tier, ...] = (
    Tier(
        id="starter", name="Starter", positioning="Basic",
        audience="Small businesses and single-focus accountants",
        monthly_price=20,
        features=(
            "Transaction Preparation & Review: vendor standardization, sales-tax review, and vendor-list reconciliation",
            "Standard CSV and Excel file uploads",
            "Basic reporting and email support",
        ),
        tool_ids=("sales-tax",),
        support="Email support",
    ),
    Tier(
        id="professional", name="Professional", positioning="Most popular",
        audience="Growing businesses and mid-sized accounting teams",
        monthly_price=100, inherits="Everything in Starter",
        features=(
            "Inventory Costing & Analytics: inventory costs, control reviews, and period closes (example workflow: strict FIFO)",
            "Data Reconciliation Studio: match two sources to the cent and review exceptions (example workflow: QuickBooks and Infinium)",
            "Planned: multi-user workspace access and saved session history",
        ),
        featured=True,
        tool_ids=("fifo-inventory", "recon"),
        users="Multiple users (planned; seat count to be confirmed)",
        support="Email support",
    ),
    Tier(
        id="enterprise", name="Enterprise", positioning="Premium",
        audience="Large organizations that need end-to-end automation",
        monthly_price=None, inherits="Everything in Professional",
        features=(
            "Prototype: Invoice Lifecycle Hub, tracking invoices from Outlook through payment (Outlook mail is simulated today)",
            "Planned: priority API integrations, such as custom QuickBooks or ERP sync",
            "Planned: advanced audit logs and custom user roles. Dedicated support is included.",
        ),
        action="Contact sales",
        tool_ids=("invoice-hub",),
        users="Custom user roles (planned)",
        support="Dedicated support",
    ),
)

def _price(tier: Tier, annual: bool) -> tuple[str, str]:
    """(headline price, unit text) for the chosen billing cycle."""
    if tier.monthly_price is None:
        return "Custom", ""
    if annual:
        return f"${tier.monthly_price * _ANNUAL_MONTHS_BILLED:,}", "per year"
    return f"${tier.monthly_price:,}", "per month"


def comparison_html() -> str:
    """Side-by-side plan table. Each tier's applications include those of the tiers below it."""
    titles = {app.url_path: app.title for app in APPS}
    apps_by_tier, running = [], []
    for t in TIERS:
        running = running + [titles[i] for i in t.tool_ids]
        apps_by_tier.append(running)

    def money(t: Tier, annual: bool) -> str:
        if t.monthly_price is None:
            return "Quoted to your organization"
        if annual:
            return (f"${t.monthly_price * _ANNUAL_MONTHS_BILLED:,} billed once a year "
                    f"(${t.monthly_price * 12 - t.monthly_price * _ANNUAL_MONTHS_BILLED:,} less than 12 monthly payments)")
        return f"${t.monthly_price:,} per month"

    rows = (
        ("Monthly price", [money(t, False) for t in TIERS]),
        ("Annual price (full charge)", [money(t, True) for t in TIERS]),
        ("Included applications", ["<br>".join(map(html.escape, a)) for a in apps_by_tier]),
        ("Users", [t.users for t in TIERS]),
        ("Organizations", [t.organizations for t in TIERS]),
        ("Processing limits", [t.processing for t in TIERS]),
        ("Saved-project storage", [t.storage for t in TIERS]),
        ("Support", [t.support for t in TIERS]),
    )
    head = "".join(f"<th scope='col'>{html.escape(t.name)}</th>" for t in TIERS)
    body = "".join(
        f"<tr><th scope='row'>{label}</th>" + "".join(f"<td>{c if label == 'Included applications' else html.escape(c)}</td>" for c in cells) + "</tr>"
        for label, cells in rows
    )
    return (
        '<table class="pp-cmp"><caption>Compare plans</caption>'
        f"<thead><tr><th scope='col'><span class='pp-sr'>Feature</span></th>{head}</tr></thead><tbody>{body}</tbody></table>"
        '<p class="pp-cmp-note">&ldquo;To be confirmed&rdquo; means that limit has not been decided; the app enforces no '
        "plan-specific limits today. Saved projects are not built yet. Prices are placeholders.</p>"
    )


_TAGGED = {"Planned: ": "planned", "Prototype: ": "prototype"}


def _feature_li(text: str) -> str:
    """One feature line. A leading "Planned: " or "Prototype: " becomes a small tag, so a benefit that is not
    built yet is never shown as an included one."""
    for prefix, kind in _TAGGED.items():
        if text.startswith(prefix):
            return (f'<li><span class="pp-tag" data-kind="{kind}">{prefix[:-2]}</span> '
                    f'{html.escape(text[len(prefix):])}</li>')
    return f"<li>{html.escape(text)}</li>"


def tier_html(tier: Tier, annual: bool, footer: str = "") -> str:
    """The card body. ``footer`` (optional HTML) sits under the features, inside the same block, so the
    card's button stays the only other element and lines up across cards."""
    headline, unit = _price(tier, annual)
    items = ([f'<li class="pp-inherit">{html.escape(tier.inherits)}</li>'] if tier.inherits else [])
    items += [_feature_li(f) for f in tier.features]
    return (
        f'<p class="pp-tier-label">{html.escape(tier.positioning)}</p>'
        f'<h3 class="pp-tier-name">{html.escape(tier.name)}</h3>'
        f'<p class="pp-tier-for">{html.escape(tier.audience)}</p>'
        f'<p class="pp-price">{headline} {f"<small>{html.escape(unit)}</small>" if unit else ""}</p>'
        f'<ul class="pp-feats">{"".join(items)}</ul>{footer}'
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


def money(cents: int) -> str:
    return f"${cents // 100:,}.{cents % 100:02d}"


def totals(tier: Tier, annual: bool) -> tuple[int, int, int]:
    """(base, tax, total) in cents. Tax is rounded half-up to the penny."""
    base = tier.monthly_price * 100 * (_ANNUAL_MONTHS_BILLED if annual else 1)
    tax = int((Decimal(base) * TAX_RATE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return base, tax, base + tax


def order_summary(tier: Tier, annual: bool) -> str:
    note = ("Online checkout is not connected yet, so nothing has been charged and no order "
            "has been placed.")
    if tier.monthly_price is None:
        return ('<div class="pp-order"><strong>Enterprise</strong> is quoted to your organization. Our '
                "team will scope the integrations, user roles, and support before agreeing a price."
                f"<br>{note}</div>")
    base, tax, total = totals(tier, annual)
    cycle = "year" if annual else "month"
    rows = (f"<tr><td>{html.escape(tier.name)} base price (per {cycle})</td><td>{money(base)}</td></tr>"
            f"<tr><td>Sales tax ({TAX_RATE * 100:.2f}%)</td><td>{money(tax)}</td></tr>"
            f'<tr class="pp-total"><td>Total</td><td>{money(total)}</td></tr>')
    # Keyed by plan and cycle so the browser re-runs the fade-in each time the total changes.
    return (f'<div class="pp-order" data-k="{tier.id}-{cycle}"><table class="pp-lines">{rows}</table>'
            f"{note}</div>")


def _register_html() -> str:
    """The charge register: one row per order recorded this session. It holds the card brand and last four
    digits only, never the number, expiry, or security code."""
    entries = st.session_state.get(REGISTER_KEY, [])
    if not entries:
        return ('<div class="pp-register"><h2>Charge register</h2><p class="pp-cmp-note">No charges yet. Orders you '
                "place appear here. Nothing is charged in this demo.</p></div>")
    items = "".join(
        f'<li><div class="pp-reg-top"><strong>{money(e["total"])}</strong><span>{html.escape(e["date"])}</span></div>'
        f'<div>{html.escape(e["plan"])} ({html.escape(e["cycle"])})</div>'
        f'<div class="pp-reg-sub">{money(e["base"])} + {money(e["tax"])} tax</div>'
        f'<div>{html.escape(e["method"])}</div>'
        f'<div class="pp-reg-status">{html.escape(e["status"])}</div></li>'
        for e in reversed(entries)
    )
    return f'<aside class="pp-register"><h2>Charge register</h2><ul>{items}</ul></aside>'


def _record(tier: Tier, annual: bool, method: str) -> None:
    base, tax, total = totals(tier, annual)
    st.session_state.setdefault(REGISTER_KEY, []).append({
        "date": datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M"),
        "plan": tier.name, "cycle": "Annual" if annual else "Monthly",
        "base": base, "tax": tax, "total": total, "method": method, "status": "Demo: not charged",
    })


def payment_section(tier: Tier, annual: bool, *, key: str = "pp-pay-form", quiet: bool = False) -> bool:
    """Payment method fields. Nothing is charged or stored: a passing form adds a register row and the
    form is cleared. The card number, expiry, and CVC are validated and then dropped.

    Returns True on the run in which an order was recorded. ``quiet`` leaves the success message to the caller
    (the landing page moves on to its confirmation step instead)."""
    st.subheader("Payment")
    st.warning("Demo checkout: no payment processor is connected, so nothing is charged. Do not enter a real card "
               "number here. To try the form, use a test number such as 4242 4242 4242 4242 with any future date.")
    method = st.radio("Payment method", _METHODS, horizontal=True, key=f"{key}-method")
    with st.form(key, clear_on_submit=True, border=True):
        label, problems, last4_note = "", (), ""
        if method == _METHODS[0]:
            name = st.text_input("Name on card", autocomplete="cc-name")
            number = st.text_input("Card number", placeholder="1234 5678 9012 3456", max_chars=23, autocomplete="off")
            c1, c2, c3 = st.columns(3)
            expiry = c1.text_input("Expiration (MM/YY)", placeholder="MM/YY", max_chars=7, autocomplete="off")
            cvc = c2.text_input("Security code (CVC)", type="password", max_chars=4, autocomplete="off")
            zip_code = c3.text_input("Billing ZIP", max_chars=10)
        elif method == _METHODS[1]:
            code = st.text_input("Gift card code", autocomplete="off")
            st.caption("Gift cards are not connected to a balance check in this demo.")
        else:
            st.info("PayPal would open its own sign-in window to approve the payment. That hand-off is not connected "
                    "in this demo.")
        submitted = st.form_submit_button("Place order (demo)", type="primary")
    if not submitted:
        return False
    if method == _METHODS[0]:
        check = validate_card(name, number, expiry, cvc)
        problems = list(check.problems)
        if not zip_code.strip():
            problems.append("Enter the billing ZIP code.")
        label = f"{check.brand} ending {check.last4}"
    elif method == _METHODS[1]:
        problems = list(validate_gift_card(code))
        label = f"Gift card ending {digits(code)[-4:]}" if not problems else ""
    else:
        label = "PayPal"
    if problems:
        for p in problems:
            st.error(p)
        return False
    _record(tier, annual, label)
    if not quiet:
        st.success("Demo order recorded in the charge register on the right. Nothing was charged, and your card details "
                   "were not saved.")
    return True


@st.dialog("Contact sales")
def contact_dialog() -> None:
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
        f"{style_tag('sales-page')}"
        '<div class="pp-sales"><p class="pp-eyebrow">Janota Fin Automatations</p>'
        "<h1>Plans &amp; Pricing</h1>"
        '<p class="pp-lede">Pick the tier that fits your accounting team. Every plan includes the '
        "applications of the tiers below it.</p></div>"
    )
    if st.button("Back to Dashboard", icon=":material/arrow_back:", key="pp-back"):
        st.session_state.pop(VIEW_KEY, None)
        st.session_state.pop(PLAN_KEY, None)
        st.rerun()

    # The charge register sits to the right of the plans, so it stays in view beside the order and payment fields.
    main, side = st.columns([3.2, 1.2], gap="large")
    with main:
        cycle = st.segmented_control("Billing", [_MONTHLY, _ANNUAL], default=_MONTHLY, key=_CYCLE_KEY)
        annual = cycle == _ANNUAL

        for tier, col in zip(TIERS, st.columns(len(TIERS), gap="large")):
            with col, st.container(key=f"pp-plan-{tier.id}", border=True):
                st.html(tier_html(tier, annual))
                if st.button(tier.action, key=f"pp-choose-{tier.id}", width="stretch",
                             type="primary" if tier.featured else "secondary"):
                    st.session_state[PLAN_KEY] = tier.id
                    if tier.monthly_price is None:
                        st.session_state.pop(_SENT_KEY, None)
                        contact_dialog()

        # Under the cards, not above them, so the hover text never crowds the choices.
        st.html(f"{_compare_bar()}<script>{_HOVER_JS}</script>", unsafe_allow_javascript=True)

        st.html(comparison_html())

        chosen = next((t for t in TIERS if t.id == st.session_state.get(PLAN_KEY)), None)
        if chosen:
            st.html(order_summary(chosen, annual))
            if chosen.monthly_price is not None:
                payment_section(chosen, annual)
    with side:
        st.html(_register_html())
