"""The template every page shares: the app registry, navigation bar, logo, suite styling, and the
account strip (greeting and account menu) for whoever is signed in.

To add an application, append an ``AppEntry`` to ``APPS``. The top navigation bar and the
Dashboard cards are both built from that list, so nothing else needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import streamlit as st

SUITE_NAME = "Panhandle Pure Accounting Apps"

_SHARED_DIR = Path(__file__).resolve().parent
_LOGO_PATH = _SHARED_DIR / "assets" / "ppl_logo.jpg"
# Transparent variant for dark sidebars: white lettering, the light-blue drop, no white box.
# Derived from ppl_logo.jpg (background keyed out, lettering recoloured white).
_LOGO_ON_DARK_PATH = _SHARED_DIR / "assets" / "ppl_logo_on_dark.png"
_THEME_PATH = _SHARED_DIR / "theme.css"
_BANNER_JS_PATH = _SHARED_DIR / "suite_banner.js"

# The account strip: filled in by suite_banner.js. Signed in it shows a greeting, the role, and an
# avatar menu (profile, invoice workspace, sign out); signed out, a Sign in button. It sits in the
# top-right corner of every page and never restricts one.
_BANNER_HTML = (
    '<div class="pp-suite-banner" data-state="out">'
    '<div class="pp-sb-hello">'
    '<span class="pp-sb-greeting" data-slot="greeting"></span>'
    '<span class="pp-sb-role" data-slot="eyebrow"></span>'
    '</div>'
    '<button type="button" class="pp-sb-signin" data-action="workspace">Sign in</button>'
    '<div class="pp-sb-menu">'
    '<button type="button" class="pp-sb-account" data-action="menu" aria-haspopup="menu" '
    'aria-expanded="false" aria-label="Account menu">'
    '<span class="pp-sb-avatar" data-slot="initials"></span><span class="pp-sb-caret" aria-hidden="true">&#9662;</span>'
    '</button>'
    '<div class="pp-sb-popover" role="menu" hidden>'
    '<div class="pp-sb-who"><strong data-slot="name"></strong><span data-slot="eyebrow"></span>'
    '<span data-slot="email"></span><span>Simulated sign-in (prototype)</span></div>'
    '<button type="button" class="pp-sb-item" role="menuitem" data-action="workspace">My invoice workspace</button>'
    '<button type="button" class="pp-sb-item" role="menuitem" data-action="signout">Sign out</button>'
    '</div></div></div>'
)


@dataclass(frozen=True)
class AppEntry:
    title: str      # shown in the navigation bar and on the Dashboard card
    icon: str       # Material icon, e.g. ":material/inventory_2:"
    script: str     # page script, relative to the repository root
    url_path: str   # the page's URL segment, e.g. /fifo-inventory
    summary: str    # one short sentence for the Dashboard card: what someone can accomplish
    action: str = "Open application"  # the card button's label, e.g. "Open inventory"
    badge: str = ""  # optional maturity label on the card, e.g. "Prototype"


APPS: list[AppEntry] = [
    AppEntry(
        title="FIFO Inventory",
        icon=":material/inventory_2:",
        script="apps/fifo_inventory/app.py",
        url_path="fifo-inventory",
        action="Open inventory",
        summary="Calculate inventory costs, review controls, and close fiscal periods.",
    ),
    AppEntry(
        title="Sales Reconciliation",
        icon=":material/compare_arrows:",
        script="apps/recon/app.py",
        url_path="recon",
        action="Open reconciliation",
        summary="Match QuickBooks and Infinium sales and review exceptions.",
    ),
    AppEntry(
        title="Sales Tax Review",
        icon=":material/receipt_long:",
        script="apps/sales_tax/app.py",
        url_path="sales-tax",
        action="Open sales tax review",
        summary="Review tax classifications and reconcile vendor transactions.",
    ),
    AppEntry(
        title="Invoice Lifecycle Hub",
        icon=":material/mark_email_unread:",
        script="apps/invoice_hub/app.py",
        url_path="invoice-hub",
        action="Open prototype",
        # One page, two tabs: the service overview and the signed-in person's invoice dashboard.
        summary="See how invoices are tracked from Outlook through payment, and open your invoice dashboard.",
        badge="Prototype",
    ),
]

DASHBOARD_SCRIPT = "apps/dashboard.py"
# Reviews & Feedback is a page in the navigation bar, not an application card on the Dashboard.
FEEDBACK_SCRIPT = "apps/feedback.py"


def build_navigation():
    """Register every page and return the one the visitor selected."""
    pages = [st.Page(DASHBOARD_SCRIPT, title="Dashboard", icon=":material/dashboard:", default=True)]
    pages += [
        st.Page(app.script, title=app.title, icon=app.icon, url_path=app.url_path)
        for app in APPS
    ]
    pages.append(st.Page(FEEDBACK_SCRIPT, title="Reviews & Feedback", icon=":material/rate_review:",
                         url_path="feedback"))
    return st.navigation(pages, position="top")


def apply_template() -> None:
    """Suite-wide chrome drawn on every page, before the page's own content."""
    if _LOGO_PATH.is_file():
        st.logo(str(_LOGO_PATH), size="large")
    try:
        st.markdown(f"<style>{_THEME_PATH.read_text(encoding='utf-8')}</style>",
                    unsafe_allow_html=True)
    except FileNotFoundError:
        pass  # The suite still works without its stylesheet, just less polished.
    try:
        banner_js = _BANNER_JS_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return  # No banner; every page still works.
    st.html(f"{_BANNER_HTML}<script>{banner_js}</script>", unsafe_allow_javascript=True)


def use_logo_on_dark_sidebar() -> None:
    """Call from a page whose sidebar is dark: shows the transparent white-lettered logo there.

    The collapsed-sidebar icon sits on the white top bar, so it keeps the standard logo.
    """
    if _LOGO_ON_DARK_PATH.is_file() and _LOGO_PATH.is_file():
        st.logo(str(_LOGO_ON_DARK_PATH), size="large", icon_image=str(_LOGO_PATH))
