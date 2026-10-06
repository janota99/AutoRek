"""Streamlit panel for reviewer decisions (Downloads tab).

Decisions are validated and applied here, in the application, against the
engine result -- record consumption and exact-cent agreement are enforced by
``matching.review_decisions``. The workbook is then regenerated from the
reviewed result. Nothing here edits an engine classification.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from .matching import (
    apply_review_decisions,
    ReconciliationResult,
    ReviewDecisionError,
)
from .matching.review_decisions import (
    ACTION_CARRY_FORWARD,
    ACTION_CONFIRM_MATCH,
    ACTION_EXCLUDE,
    ACTION_RELEASE_TO_JE,
    decisions_from_frame,
)
from .utils import clear_prepared_workbooks


_EMPTY = pd.DataFrame({
    "QuickBooks Row ID": pd.Series(dtype="object"),
    "Reviewer Action": pd.Series(dtype="object"),
    "Infinium Row IDs": pd.Series(dtype="object"),
    "Support Reference": pd.Series(dtype="object"),
    "Reason": pd.Series(dtype="object"),
    "Decision Date": pd.Series(dtype="object"),
})


def render_review_panel(result: ReconciliationResult) -> ReconciliationResult:
    """Render the decision editor; return the result the
    workbook should be built from (the reviewed copy once decisions are applied)."""
    key = f"reviewed_result_{result.run_id}"
    reviewed: ReconciliationResult = st.session_state.get(key, result)
    st.markdown("#### Reviewer decisions")
    st.caption(
        "Decisions are applied here and the workbook is regenerated -- Excel never re-checks record "
        "consumption or exact-cent agreement. Engine classifications are never changed. "
        "Release / Confirm Match / Carry Forward apply to review holds; Exclude applies to JE support "
        "(or a hold) and needs a supporting reference."
    )
    ledger = result.qb_dispositions
    reviewable = ledger.loc[ledger["Final Disposition"].isin(["REVIEW_HOLD", "TRUE_UNMATCHED"])]
    with st.expander(
        f"Reviewable rows ({len(reviewable):,}) and decisions", expanded=bool((ledger["Final Disposition"] == "REVIEW_HOLD").any()),
    ):
        st.dataframe(
            reviewable[[
                "QBO Row ID", "Final Disposition", "Classification Code", "Amount",
                "Final Reason", "Related Infinium Row IDs", "Candidate Evidence",
            ]],
            hide_index=True, width="stretch", height=240,
        )
        editor_key = f"review_editor_{result.run_id}"
        table = st.data_editor(
            st.session_state.get(f"review_table_{result.run_id}", _EMPTY),
            key=editor_key, num_rows="dynamic", width="stretch", hide_index=True,
            column_config={
                "QuickBooks Row ID": st.column_config.SelectboxColumn(options=list(reviewable["QBO Row ID"]), required=True),
                "Reviewer Action": st.column_config.SelectboxColumn(
                    options=[ACTION_CONFIRM_MATCH, ACTION_RELEASE_TO_JE, ACTION_EXCLUDE, ACTION_CARRY_FORWARD], required=True,
                ),
                "Infinium Row IDs": st.column_config.TextColumn(help="Confirm Match only; separate several with ';'."),
                "Decision Date": st.column_config.DateColumn(default=date.today()),
            },
        )
        if st.button("Apply decisions", key=f"apply_review_{result.run_id}"):
            try:
                st.session_state[key] = apply_review_decisions(result, decisions_from_frame(table))
                st.session_state[f"review_table_{result.run_id}"] = table
                clear_prepared_workbooks()
                st.success("Decisions applied. Prepare the workpaper again to include them.")
                st.rerun()
            except ReviewDecisionError as exc:
                for problem in exc.problems:
                    st.error(problem)

    if not reviewed.adjustment_bridge.empty:
        st.markdown("##### Adjustment bridge")
        st.dataframe(reviewed.adjustment_bridge.drop(columns=["Kind"]), hide_index=True, width="stretch")
    return reviewed
