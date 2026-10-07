"""The template every page shares: the app registry, navigation bar, logo, suite styling, and the
account strip (greeting and account menu) for whoever is signed in.

To add an application, append an ``AppEntry`` to ``APPS``. The top navigation bar and the
Dashboard cards are both built from that list, so nothing else needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from .styles import inject_base

SUITE_NAME = "Janota Fin Automatations Accounting Apps"

_SHARED_DIR = Path(__file__).resolve().parent
_LOGO_PATH = _SHARED_DIR / "assets" / "janota_fin_logo.png"
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
    action: str = "Open workspace"  # the card button's label; the same on every card
    badge: str = ""  # optional maturity label on the card, e.g. "Prototype"
    inputs: tuple[str, ...] = ()   # what the tool takes in; shown when the Dashboard card is hovered
    outputs: tuple[str, ...] = ()  # what it produces; shown with the inputs
    files: tuple[str, ...] = ()    # supported file types; shown under "View details"
    limits: tuple[str, ...] = ()   # known limitations; shown under "View details"


APPS: list[AppEntry] = [
    AppEntry(
        title="Inventory Costing & Analytics",
        icon=":material/inventory_2:",
        script="apps/fifo_inventory/app.py",
        url_path="fifo-inventory",
        summary="Calculate inventory costs, review controls, and close periods. Example workflow: strict FIFO.",
        inputs=("Master Grid: ending inventory by period", "Current-period receipts"),
        outputs=("PASS / REVIEW / FAIL controls", "Master Excel report", "Closed-period snapshot"),
        files=("Excel (.xlsx) or CSV",),
        limits=("Periods close in sequence, oldest first.",
                "A preview never changes the official layers; only Close & Commit does.",
                "Close & Commit is disabled while demo data is loaded."),
    ),
    AppEntry(
        title="Data Reconciliation Studio",
        icon=":material/compare_arrows:",
        script="apps/recon/app.py",
        url_path="recon",
        summary="Match two sources to the cent and review exceptions. Example workflow: QuickBooks and Infinium sales.",
        inputs=("QuickBooks sales export", "Infinium sales export", "Optional historical files"),
        outputs=("Matched and unmatched transactions", "Exceptions for review", "Reconciliation workpaper (Excel)"),
        files=("Excel (.xlsx) or CSV",),
        limits=("Amounts must agree to the cent.",
                "Ambiguous matches stay unresolved for your review; the app never picks the closest one."),
    ),
    AppEntry(
        title="Transaction Preparation & Review",
        icon=":material/receipt_long:",
        script="apps/sales_tax/app.py",
        url_path="sales-tax",
        summary="Standardize vendors, review sales tax, and reconcile vendor lists. Example workflow: the sales-tax cleanup.",
        inputs=("Source transactions (11 columns)", "Vendor mapping", "Cached trial balance"),
        outputs=("Cleaned transactions workbook", "Updated vendor mapping", "Vendor list comparison"),
        files=("Excel (.xlsx), up to 25 MB each", "CSV for the excluded-vendor list (up to 2 MB)"),
        limits=("Source and mapping files are read by column position, not by header name.",
                "The download stays disabled until the dollar control check is $0.00.",
                "The trial balance is a cache shared by everyone using the app."),
    ),
    AppEntry(
        title="Invoice Lifecycle Hub",
        icon=":material/mark_email_unread:",
        script="apps/invoice_hub/app.py",
        url_path="invoice-hub",
        # One page, two tabs: the service overview and the signed-in person's invoice dashboard.
        summary="See how invoices are tracked from Outlook through payment, and open your invoice dashboard.",
        badge="Prototype",
        inputs=("Outlook invoice mail (simulated in this prototype)",),
        outputs=("AP and AR stage tracking", "Personal invoice dashboard", "Reviews and feedback"),
        files=("None: this prototype uses simulated invoice mail",),
        limits=("Prototype: Outlook mail and sign-in are simulated in your browser.",
                "Sign-in personalizes the page; it is not access control."),
    ),
]

LANDING_SCRIPT = "apps/landing.py"
DASHBOARD_SCRIPT = "apps/dashboard.py"
DASHBOARD_URL = "workspace"
# Reviews & Feedback is a page in the navigation bar, not an application card on the Dashboard.
FEEDBACK_SCRIPT = "apps/feedback.py"


WIDGETS_MENU = "Accountant Widgets"
FEEDBACK_MENU = "Reviews & Feedback"


def build_navigation():
    """Register every page and return the one the visitor selected.

    Top navigation: Home (the landing page, at "/"), Workspace (the Dashboard), an "Accountant Widgets"
    dropdown holding the four tools, then Reviews & Feedback. Streamlit renders a named section as a dropdown. Feedback sits in a one-page
    section only to keep its place after the dropdown; suite_banner.js makes that label a direct link.
    """
    home = st.Page(LANDING_SCRIPT, title="Home", icon=":material/home:", default=True)
    dashboard = st.Page(DASHBOARD_SCRIPT, title="Workspace", icon=":material/dashboard:", url_path=DASHBOARD_URL)
    widgets = [
        st.Page(app.script, title=app.title, icon=app.icon, url_path=app.url_path)
        for app in APPS
    ]
    feedback = st.Page(FEEDBACK_SCRIPT, title="Reviews & Feedback", icon=":material/rate_review:",
                       url_path="feedback")
    return st.navigation({"": [home, dashboard], WIDGETS_MENU: widgets, FEEDBACK_MENU: [feedback]}, position="top")


def apply_template() -> None:
    """Suite-wide chrome drawn on every page, before the page's own content."""
    if _LOGO_PATH.is_file():
        st.logo(str(_LOGO_PATH), size="large")
    inject_base()
    try:
        banner_js = _BANNER_JS_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return  # No banner; every page still works.
    st.html(f"{_BANNER_HTML}<script>{banner_js}</script>", unsafe_allow_javascript=True)

