"""Declares the hub's static site (./site) as a Streamlit component.

This lives in its own importable module because Streamlit's declare_component() must be
called from one; the page script (app.py) is exec'd by st.navigation and has no module.
"""
from pathlib import Path

import streamlit.components.v1 as components

SITE_DIR = Path(__file__).resolve().parent / "site"

invoice_hub = components.declare_component("invoice_hub", path=str(SITE_DIR))
