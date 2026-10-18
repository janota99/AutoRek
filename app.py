"""Janota Fin Automations Accounting Apps: one Streamlit app hosting every tool in this folder.

Run from this folder:
    streamlit run app.py

The applications and the navigation bar are registered in shared/layout.py.
"""
import streamlit as st

from apps import demo_banner
from apps import feedback
from apps.sales_page import PLAN_KEY, VIEW_KEY
from urllib.parse import urlparse

from shared.layout import (
    DASHBOARD_URL, FAVICON_PATH, apply_template, build_navigation, title_for_path,
)


def _requested_path() -> str:
    try:
        return urlparse(st.context.url or "").path
    except Exception:  # noqa: BLE001 - no URL (a headless test run): the plain brand title is fine
        return ""


# Each page gets its own browser-tab title and the brand favicon. set_page_config has to come before
# st.navigation, so the page is read from the URL here rather than from the selected page.
st.set_page_config(
    page_title=title_for_path(_requested_path()),
    page_icon=str(FAVICON_PATH) if FAVICON_PATH.is_file() else ":material/apps:",
    layout="wide",
)

page = build_navigation()
if page.url_path != DASHBOARD_URL:  # leaving the Dashboard: close its sales page
    st.session_state.pop(VIEW_KEY, None)
    st.session_state.pop(PLAN_KEY, None)
if page.url_path:  # Home's url_path is empty; leaving Home: close its Reviews & Feedback view
    st.session_state.pop(feedback.VIEW_KEY, None)
apply_template()
demo_banner.render(page.url_path)
page.run()
