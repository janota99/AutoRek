"""Session storage for the workspace's fiscal period, and the one-shot hand-off to each tool.

The workspace page writes the calendar and the optional period-end date here. Every fiscal year's calendar that was ever
saved is kept (``calendar_for``); one of them is the *active* one the workspace shows (``get_calendar``). The file stays readable
by older code: its top-level ``year`` / ``start`` / ``period_ends`` are the active calendar, and a ``calendars`` map adds the rest.
A file without that map (one year only) loads as a map of one.

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
_CALENDARS = "suite_fiscal_calendars"
_VERIFIED = "suite_fiscal_verified"      # fiscal years whose calendar came from the saved file or an explicit Save
_END = "suite_fiscal_end"
_CONTEXT = "suite_fiscal_context"
_VERSION = "suite_fiscal_version"


def _path() -> Path:
    return Path(os.environ.get("FISCAL_SETTINGS_PATH", SETTINGS_PATH))


def _parse_calendar(year, data) -> Optional[FiscalCalendar]:
    try:
        calendar = FiscalCalendar(int(year), date.fromisoformat(data["start"]),
                                  tuple(date.fromisoformat(d) for d in data["period_ends"]))
    except (ValueError, KeyError, TypeError):
        return None
    return None if validate(calendar) else calendar


def _read_all() -> tuple[Optional[FiscalCalendar], Optional[date], dict]:
    """(active calendar, end date, {year: calendar}) from the saved file; (None, None, {}) when it is unusable. Never raises.

    An older file holds one year at the top level and nothing else: that year is the active calendar and the only one saved.
    A damaged entry in the ``calendars`` map is skipped; the others are kept."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        active = _parse_calendar(data["year"], data)
        if active is None:
            return None, None, {}
        end = data.get("end_date")
        end = date.fromisoformat(end) if end else None
    except (OSError, ValueError, KeyError, TypeError):
        return None, None, {}
    calendars = {}
    saved = data.get("calendars")
    if isinstance(saved, dict):
        for year, entry in saved.items():
            parsed = _parse_calendar(year, entry) if isinstance(entry, dict) else None
            if parsed is not None:
                calendars[parsed.year] = parsed
    calendars[active.year] = active            # the top level always wins for its own year
    return active, end, calendars


def _read_saved() -> tuple[Optional[FiscalCalendar], Optional[date]]:
    """The saved active calendar and end date; (None, None) when there is no usable file. Never raises."""
    active, end, _ = _read_all()
    return active, end


def _save() -> None:
    calendar, end = get_calendar(), get_end_date()
    data = {"year": calendar.year, "start": calendar.start.isoformat(),
            "period_ends": [d.isoformat() for d in calendar.period_ends],
            "end_date": end.isoformat() if end else None,
            "calendars": {str(year): {"start": c.start.isoformat(), "period_ends": [d.isoformat() for d in c.period_ends]}
                          for year, c in sorted(_calendars().items())}}
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
    calendar, end, calendars = _read_all()
    st.session_state[_CALENDAR] = calendar or default_calendar(date.today().year)
    st.session_state[_CALENDARS] = dict(calendars) if calendars else {st.session_state[_CALENDAR].year: st.session_state[_CALENDAR]}
    st.session_state[_END] = end
    st.session_state[_VERIFIED] = set(calendars)      # the default placeholder above is never among them
    _refresh()


def save_failed() -> bool:
    return bool(st.session_state.get("suite_fiscal_save_error"))


def get_calendar() -> FiscalCalendar:
    _ensure_loaded()
    return st.session_state[_CALENDAR]


def get_end_date() -> Optional[date]:
    _ensure_loaded()
    return st.session_state.get(_END)


def _calendars() -> dict:
    _ensure_loaded()
    return st.session_state.setdefault(_CALENDARS, {st.session_state[_CALENDAR].year: st.session_state[_CALENDAR]})


def saved_years() -> list[int]:
    """Every fiscal year with a saved calendar, oldest first."""
    return sorted(_calendars())


def calendar_for(year: int) -> Optional[FiscalCalendar]:
    """The calendar saved for exactly that fiscal year, or None."""
    return _calendars().get(int(year))


def verified_calendar(year: int) -> Optional[FiscalCalendar]:
    """That fiscal year's calendar only if a person saved it (loaded from the saved file, or saved this session), else None.

    The default placeholder the workspace shows before any calendar is entered is never verified, so a tool that needs
    authoritative period dates uses this rather than ``calendar_for_year``."""
    _ensure_loaded()
    if int(year) not in st.session_state.get(_VERIFIED, set()):
        return None
    return calendar_for(year)


def calendar_for_year(year: int) -> FiscalCalendar:
    """The calendar a tool should use for the fiscal year it is processing: that year's saved calendar, else the active one
    (which a tool then recognizes as not covering the year)."""
    return calendar_for(year) or get_calendar()


def set_calendar(calendar: FiscalCalendar) -> None:
    """Save this year's calendar (every other year's stays) and make it the active one."""
    _ensure_loaded()
    calendars = _calendars()
    st.session_state.setdefault(_VERIFIED, set()).add(int(calendar.year))
    if calendar == st.session_state[_CALENDAR] and calendars.get(calendar.year) == calendar:
        return
    calendars[calendar.year] = calendar
    st.session_state[_CALENDAR] = calendar
    _refresh()
    _save()


def use_calendar(year: int) -> bool:
    """Make a saved year the active calendar (nothing is changed or lost). False if that year was never saved."""
    saved = calendar_for(year)
    if saved is None:
        return False
    if saved != st.session_state[_CALENDAR]:
        st.session_state[_CALENDAR] = saved
        _refresh()
        _save()
    return True


def calendar_from_edit(year: int, start: date, ends: tuple) -> FiscalCalendar:
    """The calendar a workspace edit means. An edited table is taken as typed. Only the year or start changed (table left as it
    was): if that year is already saved with that start, it is that saved calendar (so a five-week Period 13 survives a visit);
    otherwise thirteen four-week periods are built from the start."""
    active = get_calendar()
    if tuple(ends) == active.period_ends and (int(year), start) != (active.year, active.start):
        saved = calendar_for(year)
        if saved is not None and saved.start == start:
            return saved
        return default_calendar(int(year), start)
    return FiscalCalendar(int(year), start, tuple(ends))


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
    end = get_end_date()
    context = resolve(get_calendar(), end)
    if end is not None and (context is None or context.period is None):
        for other in _calendars().values():             # a date from another saved fiscal year still resolves
            found = resolve(other, end)
            if found is not None and found.period is not None:
                context = found
                break
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
