"""Session storage for the workspace's fiscal period, and the one-shot hand-off to each tool.

The workspace page writes the calendar and the optional period-end date here. A tool calls
``pending_for(<tool>)`` before it draws its own fiscal widgets; it gets the context once per change
(None if the field is blank or already applied) and sets its widgets from it. After that the tool's
own controls win until the workspace value changes again.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import streamlit as st

from .fiscal_calendar import FiscalCalendar, FiscalContext, default_calendar, resolve

_CALENDAR = "suite_fiscal_calendar"
_END = "suite_fiscal_end"
_CONTEXT = "suite_fiscal_context"
_VERSION = "suite_fiscal_version"


def get_calendar() -> FiscalCalendar:
    if _CALENDAR not in st.session_state:
        st.session_state[_CALENDAR] = default_calendar(date.today().year)
    return st.session_state[_CALENDAR]


def get_end_date() -> Optional[date]:
    return st.session_state.get(_END)


def set_calendar(calendar: FiscalCalendar) -> None:
    st.session_state[_CALENDAR] = calendar
    _refresh()


def set_end_date(end: Optional[date]) -> None:
    st.session_state[_END] = end
    _refresh()


def current() -> Optional[FiscalContext]:
    return st.session_state.get(_CONTEXT)


def _refresh() -> None:
    context = resolve(get_calendar(), get_end_date())
    if context != st.session_state.get(_CONTEXT):
        st.session_state[_CONTEXT] = context
        st.session_state[_VERSION] = st.session_state.get(_VERSION, 0) + 1


def pending_for(tool: str) -> Optional[FiscalContext]:
    """The workspace's fiscal context if this tool has not applied the current one yet, else None."""
    context = current()
    version = st.session_state.get(_VERSION, 0)
    marker = f"_suite_fiscal_applied_{tool}"
    if context is None or st.session_state.get(marker) == version:
        return None
    st.session_state[marker] = version
    return context
