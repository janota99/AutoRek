"""Sales Tax Vendor Review Tool: a page of the combined app.

Two tools, picked in the sidebar:
  - Transaction Cleanup (transaction_cleanup.py, with its logic in cleanup.py and its
    Excel output in excel_output.py)
  - Vendor Reconciliation (vendor_reconciliation.py)
File reading and the trial balance cache live in ingestion.py.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from apps.sales_tax.transaction_cleanup import render_transaction_cleanup
from apps.sales_tax.vendor_reconciliation import render_vendor_reconciliation


# =====================================================================
# STREAMLIT UI
# =====================================================================

def load_css(path: str = "style.css"):
    # Resolved against this file's directory rather than the process's
    # current working directory - Streamlit doesn't guarantee it's launched
    # from the app's own folder, so a plain relative path can silently fail
    # to find style.css depending on how/where `streamlit run` was invoked.
    css_path = Path(__file__).resolve().parent / path
    try:
        with open(css_path) as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        pass  # App still works without the stylesheet; just less polished.


def main():
    # Page title and wide layout come from st.navigation in the root app.py.
    load_css("style.css")

    # Initialize session state for navigation if it doesn't exist
    if "active_tool" not in st.session_state:
        st.session_state["active_tool"] = "Transaction Cleanup"

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

    st.sidebar.title("Sales Tax Vendor Review Tool")

    # Bind the radio button to session_state for dynamic tool switching
    tool = st.sidebar.radio(
        "Tool",
        ["Transaction Cleanup", "Vendor Reconciliation"],
        key="active_tool"
    )
    st.sidebar.divider()

    if tool == "Transaction Cleanup":
        render_transaction_cleanup()
    else:
        render_vendor_reconciliation()


if __name__ == "__main__":
    main()
