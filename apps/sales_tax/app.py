"""Sales Tax Vendor Review Tool: a page of the combined app.

Three tools, picked with the segmented control:
  - Transaction Cleanup (transaction_cleanup.py, with its logic in cleanup.py and its
    Excel output in excel_output.py)
  - Vendor Reconciliation (vendor_reconciliation.py)
  - Sales Tax Calculator (tax_calculator_ui.py, with its logic in tax_calculator.py)
File reading and the trial balance cache live in ingestion.py.
"""

from __future__ import annotations

import streamlit as st

from shared.styles import inject_page
from apps.sales_tax import ui
from apps.sales_tax.tax_calculator_ui import render_tax_calculator
from apps.sales_tax.transaction_cleanup import render_transaction_cleanup
from apps.sales_tax.vendor_reconciliation import render_vendor_reconciliation


TOOLS = ["Transaction Cleanup", "Vendor Reconciliation", "Sales Tax Calculator"]


# =====================================================================
# STREAMLIT UI
# =====================================================================

def main():
    # Page title and wide layout come from st.navigation in the root app.py.
    inject_page("sales-tax")

    # Initialize session state for navigation if it doesn't exist
    if "active_tool" not in st.session_state:
        st.session_state["active_tool"] = TOOLS[0]

    # Applies any pending navigation request BEFORE the radio widget below is
    # instantiated. Nothing currently sets "_nav_request" (the old "Go to
    # Vendor Reconciliation Tool" button that used it was replaced by inline
    # new-vendor classification on the results screen), but this is kept as
    # the correct pattern for any future button that needs to switch tools
    # programmatically: writing directly to st.session_state["active_tool"]
    # after the radio widget has been instantiated in the same run raises a
    # StreamlitAPIException, since that key belongs to the widget once it
    # exists - stashing the request under a different key and applying it
    # here, before the radio is created, avoids that.
    if "_nav_request" in st.session_state:
        st.session_state["active_tool"] = st.session_state.pop("_nav_request")

    ui.page_header(
        "Transaction Preparation & Review",
        "Standardize vendors, review sales tax, and reconcile vendor lists. Every change is traceable and the "
        "download stays locked until the dollar control check is $0.00.",
        eyebrow="Example workflow: sales-tax cleanup",
    )
    # Bound to session_state so a future button can switch tools (see _nav_request above). A segmented control
    # can be deselected; fall back to the first tool if it is.
    st.segmented_control("Tool", TOOLS, key="active_tool", label_visibility="collapsed")
    tool = st.session_state.get("active_tool") or TOOLS[0]

    st.sidebar.markdown("### Transaction Preparation & Review")
    if tool == TOOLS[0]:
        render_transaction_cleanup()
    elif tool == TOOLS[2]:
        render_tax_calculator()
    else:
        st.sidebar.caption("Vendor Reconciliation compares two vendor listings by Vendor ID. It does not use the trial balance.")
        render_vendor_reconciliation()


if __name__ == "__main__":
    main()
