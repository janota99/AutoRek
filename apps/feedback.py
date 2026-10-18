"""Reviews & Feedback: sample reviews and a feedback form, opened only from the Home page.

Like the Dashboard's Plans page it is not a registered page: it has no URL and no navigation entry. Home's footer link
sets ``VIEW_KEY`` and Home draws this view in place of itself; app.py clears the flag when the visitor leaves Home.
It embeds the Invoice Hub's feedback page (the hub's static site, see apps/invoice_hub/component.py). The form needs
no sign-in; anything typed is stored in this browser only.
"""
import streamlit as st

from apps.invoice_hub.component import invoice_hub

VIEW_KEY = "pp_l_view"      # session_state: "feedback" while the Reviews & Feedback view is showing on Home
FEEDBACK_VIEW = "feedback"


def is_open() -> bool:
    return st.session_state.get(VIEW_KEY) == FEEDBACK_VIEW


def render() -> None:
    """Draw the Reviews & Feedback view. Call only from Home."""
    if st.button("Back to Home", icon=":material/arrow_back:", key="pp-feedback-back"):
        st.session_state.pop(VIEW_KEY, None)
        st.rerun()
    # The component always loads index.html; page= opens the feedback page instead (see site/streamlit-bridge.js).
    invoice_hub(page="feedback.html", key="feedback")
