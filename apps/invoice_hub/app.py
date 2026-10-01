"""Invoice Lifecycle Hub page.

The hub is a static HTML/JavaScript site (./site). Streamlit serves that folder as a component
(see component.py), so the site's own pages, scripts, and links work unchanged inside this page,
and its localStorage data stays in the visitor's browser. site/streamlit-bridge.js sizes the frame.
"""
from apps.invoice_hub.component import invoice_hub

invoice_hub(key="invoice_hub")
