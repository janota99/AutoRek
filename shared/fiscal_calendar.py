"""Fiscal calendar logic shared by the workspace page and the tools that take a fiscal period.

Pure: no Streamlit. A calendar is one fiscal year: a label, the first day of Period 1, and the
end date of each of its 13 periods. Nothing here is a business rule the books depend on; the
default (13 four-week periods) only pre-fills a table the user can see and edit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

PERIOD_COUNT = 13
DEFAULT_PERIOD_DAYS = 28


@dataclass(frozen=True)
class FiscalCalendar:
    year: int
    start: date
    period_ends: tuple[date, ...]


@dataclass(frozen=True)
class FiscalContext:
    """What the workspace hands to a tool. ``fiscal_year`` and ``period`` are None when the date falls outside the calendar."""
    end_date: date
    fiscal_year: Optional[int]
    period: Optional[int]


def default_period_ends(start: date, days: int = DEFAULT_PERIOD_DAYS) -> tuple[date, ...]:
    return tuple(start + timedelta(days=days * n - 1) for n in range(1, PERIOD_COUNT + 1))


def default_calendar(year: int, start: Optional[date] = None) -> FiscalCalendar:
    start = start or date(year, 1, 1)
    return FiscalCalendar(year, start, default_period_ends(start))


def validate(calendar: FiscalCalendar) -> Optional[str]:
    """A plain-language problem with the calendar, or None when it is usable."""
    if len(calendar.period_ends) != PERIOD_COUNT:
        return f"A fiscal year needs {PERIOD_COUNT} period end dates."
    previous = calendar.start - timedelta(days=1)
    for number, end in enumerate(calendar.period_ends, start=1):
        if end <= previous:
            return f"Period {number:02d} must end after {previous:%Y-%m-%d}."
        previous = end
    return None


def locate(calendar: FiscalCalendar, day: date) -> Optional[int]:
    """The period (1-13) whose dates contain ``day``, or None if it is outside the calendar."""
    if validate(calendar) or day < calendar.start:
        return None
    for number, end in enumerate(calendar.period_ends, start=1):
        if day <= end:
            return number
    return None


def resolve(calendar: FiscalCalendar, end_date: Optional[date]) -> Optional[FiscalContext]:
    """The context for an entered period-end date; None when the field is blank."""
    if end_date is None:
        return None
    period = locate(calendar, end_date)
    if period is None:
        return FiscalContext(end_date, None, None)
    return FiscalContext(end_date, calendar.year, period)
