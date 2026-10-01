"""Dashboard: the landing page, with one card per application, built from the registry in shared/layout.py."""
import html

import streamlit as st

from shared.layout import APPS, SUITE_NAME

st.markdown(
    f'<div class="suite-hero"><h1>{html.escape(SUITE_NAME)}</h1>'
    "<p>Pick an application below, or use the navigation bar at the top of any page "
    "to switch between them. Switching apps clears the files you uploaded on the page you "
    "leave, so to work in two apps at once, open each in its own browser tab.</p></div>",
    unsafe_allow_html=True,
)

columns = st.columns(2, gap="medium")
for index, app in enumerate(APPS):
    with columns[index % 2]:
        with st.container(border=True):
            st.markdown(
                f'<p class="suite-card-title">{html.escape(app.title)}</p>'
                f'<p class="suite-card-summary">{html.escape(app.summary)}</p>',
                unsafe_allow_html=True,
            )
            st.page_link(app.script, label=f"Open {app.title}", icon=app.icon)
