"""The template every page shares: the app registry, navigation bar, logo, and suite styling.

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
_THEME_PATH = _SHARED_DIR / "theme.css"


@dataclass(frozen=True)
class AppEntry:
    title: str      # shown in the navigation bar and on the Dashboard card
    icon: str       # Material icon, e.g. ":material/inventory_2:"
    script: str     # page script, relative to the repository root
    url_path: str   # the page's URL segment, e.g. /fifo-inventory
    summary: str    # one or two sentences for the Dashboard card


APPS: list[AppEntry] = [
    AppEntry(
        title="FIFO Inventory",
        icon=":material/inventory_2:",
        script="apps/fifo_inventory/app.py",
        url_path="fifo-inventory",
        summary="13-period strict FIFO costing for raw materials: upload the Master Grid and "
                "receipts, preview PASS/REVIEW/FAIL controls, and close periods in sequence.",
    ),
    AppEntry(
        title="Sales Reconciliation",
        icon=":material/compare_arrows:",
        script="apps/recon/app.py",
        url_path="recon",
        summary="Match QuickBooks sales to Infinium by PO, invoice, and exact signed amount, "
                "then export the reconciliation workpaper.",
    ),
    AppEntry(
        title="Sales Tax Review",
        icon=":material/receipt_long:",
        script="apps/sales_tax/app.py",
        url_path="sales-tax",
        summary="Clean up sales-tax transactions against the vendor mapping and trial balance, "
                "and reconcile two vendor listings.",
    ),
    AppEntry(
        title="Invoice Lifecycle Hub",
        icon=":material/mark_email_unread:",
        script="apps/invoice_hub/app.py",
        url_path="invoice-hub",
        summary="Prototype Outlook invoice tracker: route customer invoices and vendor bills into "
                "queues and track how long each sits at every approval stage.",
    ),
]

DASHBOARD_SCRIPT = "apps/dashboard.py"


def build_navigation():
    """Register every page and return the one the visitor selected."""
    pages = [st.Page(DASHBOARD_SCRIPT, title="Dashboard", icon=":material/dashboard:", default=True)]
    pages += [
        st.Page(app.script, title=app.title, icon=app.icon, url_path=app.url_path)
        for app in APPS
    ]
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
