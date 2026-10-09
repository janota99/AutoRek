"""The demo banner: shown above a solution's tool when it was opened from a landing-page "Try ..." button
(or the Dashboard's "Try demo"), i.e. with ``?demo=1`` in the address.

It names the solution with the same title and icon as the landing page, says what sample data is loaded
and where to look, and offers "Back to solutions", which returns to the landing page's solution explorer.
It changes no data: ``shared/sample_data.py`` already loads the samples for ``?demo=1``.
"""
from __future__ import annotations

import html

import streamlit as st

from apps.landing_data import SOLUTION_BY_APP
from shared.layout import LANDING_SCRIPT

SECTION_PARAM = "to"          # landing page: ?to=solutions scrolls to the solution explorer
SOLUTIONS_SECTION = "solutions"


def render(url_path: str) -> None:
    """Draw the banner for the page at ``url_path`` if it is a solution opened in demo mode."""
    solution = SOLUTION_BY_APP.get(url_path)
    if solution is None or st.query_params.get("demo") != "1":
        return
    with st.container(key="pp-demo-bar"):
        text, back = st.columns([5, 1.4], vertical_alignment="center")
        text.html(
            f'<div class="pp-demo-copy"><span class="pp-demo-tag">Demo &middot; sample data</span>'
            f'<h2><span class="material-symbols-rounded" aria-hidden="true">{html.escape(solution.icon)}</span>'
            f'{html.escape(solution.name)}</h2><p>{html.escape(solution.demo_hint)}</p></div>'
        )
        if back.button("Back to solutions", key="pp-demo-back", icon=":material/arrow_back:", width="stretch"):
            st.switch_page(LANDING_SCRIPT, query_params={SECTION_PARAM: SOLUTIONS_SECTION})
