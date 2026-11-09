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


def suggest_period_ends(start: date, period13_days: int = DEFAULT_PERIOD_DAYS) -> tuple[date, ...]:
    """Twelve four-week periods from ``start``, then Period 13 of ``period13_days`` (28, or 35 for a five-week year)."""
    twelve = default_period_ends(start)[:PERIOD_COUNT - 1]
    return twelve + (twelve[-1] + timedelta(days=period13_days),)


def schedule_problems(start: date, ends) -> list[str]:
    """Every plain-language problem with a schedule, each naming the dates in conflict. Empty means usable.

    Checks exactly what was given, in order: thirteen dates, each one after the day before Period 1 starts (Period 1) or after
    the previous period's end."""
    ends = list(ends)
    if len(ends) != PERIOD_COUNT:
        return [f"A fiscal year needs {PERIOD_COUNT} period end dates; {len(ends)} were given."]
    problems, previous, label = [], start, f"Period 1 start {start:%Y-%m-%d}"
    for number, end in enumerate(ends, start=1):
        if end is None:
            problems.append(f"Period {number:02d} has no end date.")
            continue
        if end < previous or (number > 1 and end == previous) or (number == 1 and end < start):
            if number == 1:
                problems.append(f"Period 01 ends {end:%Y-%m-%d}, before it starts ({start:%Y-%m-%d}).")
            else:
                problems.append(f"Period {number:02d} ends {end:%Y-%m-%d}, which is not after {label} ({previous:%Y-%m-%d}).")
        else:
            previous, label = end, f"Period {number:02d}'s end"
    return problems


def parse_period_end_dates(text: str) -> tuple[Optional[tuple[date, ...]], Optional[str]]:
    """(dates, None) for pasted text holding exactly thirteen dates, else (None, the reason).

    Dates may be separated by new lines, tabs, commas or semicolons, and written as 2025-10-25, 10/25/2025 or 10/25/25
    (month first). A cell that is not a date is named, never skipped."""
    import re
    separators = "[" + chr(10) + chr(13) + chr(9) + ",;]+"
    pieces = [p.strip() for p in re.split(separators, text or "") if p.strip()]
    dates = []
    for position, piece in enumerate(pieces, start=1):
        parsed = None
        for pattern, order in ((r"^(\d{4})-(\d{1,2})-(\d{1,2})$", "ymd"), (r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$", "mdy")):
            m = re.match(pattern, piece)
            if m:
                a, b, c = (int(g) for g in m.groups())
                y, mo, d = (a, b, c) if order == "ymd" else ((c + 2000 if c < 100 else c), a, b)
                try:
                    parsed = date(y, mo, d)
                except ValueError:
                    parsed = None
        if parsed is None:
            return None, f"Entry {position} ('{piece}') is not a date. Use 2025-10-25 or 10/25/2025."
        dates.append(parsed)
    if len(dates) != PERIOD_COUNT:
        return None, f"Found {len(dates)} dates; paste exactly {PERIOD_COUNT}, one per period."
    return tuple(dates), None


def validate(calendar: FiscalCalendar) -> Optional[str]:
    """A plain-language problem with the calendar, or None when it is usable."""
    problems = schedule_problems(calendar.start, calendar.period_ends)
    return problems[0] if problems else None


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
