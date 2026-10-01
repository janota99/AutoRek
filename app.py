"""Panhandle Pure Accounting Apps: one Streamlit app hosting every tool in this folder.

Run from this folder:
    streamlit run app.py

The applications and the navigation bar are registered in shared/layout.py.
"""
import streamlit as st

from shared.layout import SUITE_NAME, apply_template, build_navigation

st.set_page_config(page_title=SUITE_NAME, page_icon=":material/apps:", layout="wide")

page = build_navigation()
apply_template()
page.run()
