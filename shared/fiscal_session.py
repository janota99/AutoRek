"""Session storage for the workspace's fiscal period, and the one-shot hand-off to each tool.

The workspace page writes the calendar and the optional period-end date here. A tool calls
``pending_for(<tool>)`` before it draws its own fiscal widgets; it gets the context once per change
(None if the field is blank or already applied) and sets its widgets from it. After that the tool's
own controls win until the workspace value changes again.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Optional

import streamlit as st

from .fiscal_calendar import FiscalCalendar, FiscalContext, default_calendar, resolve, validate

# Saved on the server's disk like FIFO's app_settings.json, so it is shared by everyone using this copy of the app
# and survives restarts. Real company settings: gitignored. FISCAL_SETTINGS_PATH overrides it (tests do).
SETTINGS_PATH = Path(os.environ.get("FISCAL_SETTINGS_PATH", Path(__file__).resolve().parent / "fiscal_settings.json"))
_LOADED = "suite_fiscal_loaded"
_CALENDAR = "suite_fiscal_calendar"
_END = "suite_fiscal_end"
_CONTEXT = "suite_fiscal_context"
_VERSION = "suite_fiscal_version"


def _path() -> Path:
    return Path(os.environ.get("FISCAL_SETTINGS_PATH", SETTINGS_PATH))


def _read_saved() -> tuple[Optional[FiscalCalendar], Optional[date]]:
    """The saved calendar and end date; (None, None) when there is no usable file. Never raises."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        calendar = FiscalCalendar(
            int(data["year"]), date.fromisoformat(data["start"]),
            tuple(date.fromisoformat(d) for d in data["period_ends"]))
        if validate(calendar):
            return None, None
        end = data.get("end_date")
        return calendar, date.fromisoformat(end) if end else None
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def _save() -> None:
    calendar, end = get_calendar(), get_end_date()
    data = {"year": calendar.year, "start": calendar.start.isoformat(),
            "period_ends": [d.isoformat() for d in calendar.period_ends],
            "end_date": end.isoformat() if end else None}
    path = _path()
    try:
        if json.loads(path.read_text(encoding="utf-8")) == data:
            return
    except (OSError, ValueError):
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as tmp:
            json.dump(data, tmp, indent=2)
        os.replace(tmp.name, path)
    except OSError:
        st.session_state["suite_fiscal_save_error"] = True   # the page says so; the session still works


def _ensure_loaded() -> None:
    """First use in a session: start from the saved settings, else the default calendar and no date."""
    if st.session_state.get(_LOADED):
        return
    st.session_state[_LOADED] = True
    calendar, end = _read_saved()
    st.session_state[_CALENDAR] = calendar or default_calendar(date.today().year)
    st.session_state[_END] = end
    _refresh()


def save_failed() -> bool:
    return bool(st.session_state.get("suite_fiscal_save_error"))


def get_calendar() -> FiscalCalendar:
    _ensure_loaded()
    return st.session_state[_CALENDAR]


def get_end_date() -> Optional[date]:
    _ensure_loaded()
    return st.session_state.get(_END)


def set_calendar(calendar: FiscalCalendar) -> None:
    _ensure_loaded()
    if calendar == st.session_state[_CALENDAR]:
        return
    st.session_state[_CALENDAR] = calendar
    _refresh()
    _save()


def set_end_date(end: Optional[date]) -> None:
    _ensure_loaded()
    if end == st.session_state.get(_END):
        return
    st.session_state[_END] = end
    _refresh()
    _save()


def current() -> Optional[FiscalContext]:
    _ensure_loaded()
    return st.session_state.get(_CONTEXT)


def _refresh() -> None:
    context = resolve(get_calendar(), get_end_date())
    if context != st.session_state.get(_CONTEXT):
        st.session_state[_CONTEXT] = context
        st.session_state[_VERSION] = st.session_state.get(_VERSION, 0) + 1


def pending_for(tool: str) -> Optional[FiscalContext]:
    """The workspace's fiscal context if this tool has not applied the current one yet, else None."""
    context = current()   # also loads the saved settings on a session's first call
    version = st.session_state.get(_VERSION, 0)
    marker = f"_suite_fiscal_applied_{tool}"
    if context is None or st.session_state.get(marker) == version:
        return None
    st.session_state[marker] = version
    return context
