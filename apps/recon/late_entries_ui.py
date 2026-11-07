"""The "Late and backdated entries" panel on the Recon page: Streamlit glue around ``late_entries``.

Optional and read-only. It reports and offers a CSV; it does not touch the reconciliation, its controls, or its
workbook. Findings are not saved anywhere: the earlier pull is an upload for this session only.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

import pandas as pd
import streamlit as st

from shared import fiscal_session
from .ingestion import detect_header_row, filter_qb_subtotal_rows, list_source_sheets, read_source_file
from .late_entries import (
    DEFAULT_CLOSE_DAYS, PullComparison, compare_pulls, detect_created_column, find_late_entries,
)
from .run_log import _csv_safe

NONE_CHOICE = "(none)"


def _csv(frame: pd.DataFrame) -> bytes:
    return frame.astype(object).map(_csv_safe).to_csv(index=False).encode("utf-8-sig")


def _created_choice(label: str, frame: pd.DataFrame, key: str) -> Optional[str]:
    columns = [str(c) for c in frame.columns]
    default = detect_created_column(columns)
    options = [NONE_CHOICE, *columns]
    picked = st.selectbox(f"{label} created date", options, key=key,
                          index=options.index(default) if default in options else 0,
                          help="The column holding when each entry was created or entered, not the transaction date.")
    return None if picked == NONE_CHOICE else picked


def _late_section(label: str, frame: pd.DataFrame, mapping: Mapping[str, Optional[str]], created: Optional[str],
                  calendar, fiscal_period: Optional[int], close_days: int, key: str) -> None:
    result = find_late_entries(frame, dataset=label, mapping=mapping, created_column=created, calendar=calendar,
                               selected_period=fiscal_period, close_days=close_days)
    st.markdown(f"**{label}**")
    if result.note:
        st.caption(result.note)
        return
    (st.warning if result.status == "WARNING" else st.success)(result.details)
    if len(result.rows):
        st.dataframe(result.rows, width="stretch", hide_index=True)
        st.download_button(f"Download {label} late entries (CSV)", _csv(result.rows),
                           file_name=f"late_entries_{key}.csv", mime="text/csv", key=f"late_dl_{key}")


def _read_earlier(file: Any, source: str, mapping: Mapping[str, Optional[str]], key: str) -> Optional[pd.DataFrame]:
    data = file.getvalue()
    sheets = list_source_sheets(data, file.name)
    sheet = st.selectbox(f"Worksheet in {file.name}", list(sheets), key=f"late_sheet_{key}") if len(sheets) > 1 else (
        sheets[0] if sheets else None)
    raw = read_source_file(data, file.name, detect_header_row(data, file.name, source, sheet), sheet)
    return filter_qb_subtotal_rows(raw, dict(mapping))[0] if source == "QB" else raw


def _compare_section(label: str, source: str, current: pd.DataFrame, mapping: Mapping[str, Optional[str]],
                     created: Optional[str], key: str) -> None:
    file = st.file_uploader(f"Earlier pull of the {label} export for this same period", type=["xlsx", "xlsm", "csv"],
                            key=f"late_earlier_{key}",
                            help="An export of this same period taken before, for example the one you reconciled "
                                 "last time. It is compared with the current file and is not saved.")
    if file is None:
        return
    try:
        earlier = _read_earlier(file, source, mapping, key)
        result: PullComparison = compare_pulls(earlier, current, mapping=mapping, created_column=created)
    except Exception as exc:  # noqa: BLE001 - a bad earlier file must never stop the page
        st.warning(f"The earlier {label} file could not be compared: {exc}")
        return
    (st.warning if result.status == "WARNING" else st.success)(result.details)
    if len(result.rows):
        st.dataframe(result.rows, width="stretch", hide_index=True)
        st.download_button(f"Download {label} changes since the earlier pull (CSV)", _csv(result.rows),
                           file_name=f"pull_changes_{key}.csv", mime="text/csv", key=f"late_diff_dl_{key}")


def render_late_entry_checks(qb: pd.DataFrame, inf: pd.DataFrame, qb_mapping: Mapping[str, Optional[str]],
                             inf_mapping: Mapping[str, Optional[str]], fiscal_year: int,
                             fiscal_period: Optional[int]) -> None:
    with st.expander("Late and backdated entries (optional check)", expanded=False):
        st.caption(
            "Finds entries that reached a period after it closed. Nothing here changes the reconciliation or its "
            "controls. A row is flagged when it was created after the end of an earlier fiscal period "
            "plus the close days below, using the fiscal calendar from the Workspace page.")
        close_days = int(st.number_input("Close days allowed after a period ends", min_value=0, max_value=60,
                                         value=DEFAULT_CLOSE_DAYS, step=1, key="late_close_days"))
        col_qb, col_inf = st.columns(2)
        with col_qb:
            qb_created = _created_choice("QuickBooks", qb, "late_created_qb")
        with col_inf:
            inf_created = _created_choice("Infinium", inf, "late_created_inf")

        st.markdown("##### 1. Entries created after their period closed")
        calendar = fiscal_session.get_calendar()
        if calendar.year != fiscal_year:
            st.info(f"The fiscal calendar on the Workspace page is for {calendar.year}; the fiscal year selected "
                    f"here is {fiscal_year}. Set the calendar for {fiscal_year} to run this check.")
        else:
            _late_section("QuickBooks", qb, qb_mapping, qb_created, calendar, fiscal_period, close_days, "qb")
            _late_section("Infinium", inf, inf_mapping, inf_created, calendar, fiscal_period, close_days, "inf")

        st.markdown("##### 2. Changes since an earlier pull")
        st.caption("Upload an earlier export of the same period. Rows added, missing, or changed since then are "
                   "listed, with the change in the period total. A row is shown as changed only when it is the one "
                   "leftover for its PO and invoice; anything less certain is listed as missing plus added.")
        _compare_section("QuickBooks", "QB", qb, qb_mapping, qb_created, "qb")
        _compare_section("Infinium", "INF", inf, inf_mapping, inf_created, "inf")
