"""The Workspace calendar is authoritative and every legitimate receipt is recognized exactly once."""
from datetime import date, datetime

import pandas as pd
import pytest

from apps.fifo_inventory.intake import PeriodWindow, calendar_problem, receipt_register_counts, register_entries
from shared.fiscal_calendar import FiscalCalendar, default_calendar

from .test_receipt_register import (
    CUTOFF, FUTURE, IN_P12, LATE_AFTER_CUTOFF, P12, P13, _prepare,
)
from .test_workbook_import import CALENDAR, _log_row

EARLY_WINDOW = PeriodWindow(end=date(2026, 8, 29), start=date(2026, 8, 3), source="calendar")


# --- the calendar is the authority; missing or broken blocks, nothing is defaulted ----------------------------

def test_a_missing_wrong_year_or_inconsistent_calendar_blocks_with_a_reason():
    assert calendar_problem(None, 2026) and "No saved fiscal calendar exists for FY2026" in calendar_problem(None, 2026)
    assert "FY2027" in calendar_problem(FiscalCalendar(2027, date(2026, 10, 4), CALENDAR.period_ends), 2026)
    backwards = FiscalCalendar(2026, CALENDAR.start, tuple(reversed(CALENDAR.period_ends)))
    assert "not usable" in calendar_problem(backwards, 2026)
    short = FiscalCalendar(2026, CALENDAR.start, CALENDAR.period_ends[:12])
    assert "not usable" in calendar_problem(short, 2026)
    assert calendar_problem(CALENDAR, 2026) is None


def test_the_unsaved_default_calendar_is_never_verified(monkeypatch, tmp_path):
    from shared import fiscal_session
    monkeypatch.setenv("FISCAL_SETTINGS_PATH", str(tmp_path / "fiscal.json"))
    monkeypatch.setattr(fiscal_session.st, "session_state", {})
    today_year = date.today().year
    assert fiscal_session.verified_calendar(today_year) is None            # the placeholder the Workspace shows first
    assert fiscal_session.calendar_for(today_year) is not None             # (it exists, but is not authority)
    fiscal_session.set_calendar(CALENDAR)
    assert fiscal_session.verified_calendar(2026) == CALENDAR
    monkeypatch.setattr(fiscal_session.st, "session_state", {})            # a new session reads the saved file
    assert fiscal_session.verified_calendar(2026) == CALENDAR


def test_receipts_are_placed_by_the_calendar_window_not_by_a_workbook_date():
    # operations' sheet may call P12 something else; the window handed in comes from the calendar only.
    valid, errors, *_ = _prepare([IN_P12, _log_row("Preforms", datetime(2026, 8, 31), 5, 1.0, po="7", invoice="Inv#7")])
    assert valid["QUANTITY"].tolist() == [1_000] and errors == []          # Aug 31 is P13 by the calendar: pending


# --- mislabeled historical receipts ----------------------------------------------------------------------------

def test_a_mislabelled_historical_receipt_never_accounted_for_is_included_with_a_flag_and_keeps_its_evidence():
    stray = _log_row("Preforms", datetime(2026, 7, 20), 800, 40.0, period="P.9 FYE 2026", invoice="Inv#M", po="M")
    valid, errors, _w, debug = _prepare([IN_P12, stray], cutoff=date(2026, 7, 1), cutoff_period=10)
    assert errors == [] and sorted(valid["QUANTITY"]) == [800, 1_000]
    assert len(debug["late_rows"]) == 1
    entry = next(e for e in register_entries(valid, "FY2026-P12") if e["qty"] == 800)
    assert entry["delivered"] == "2026-07-20" and entry["reported_period"] == "P.9 FYE 2026"
    assert entry["recognized_in"] == "FY2026-P12" and entry["late"] is True and entry["source_row"]


# --- previously processed receipts with the wrong period label -------------------------------------------------

def test_a_receipt_already_recognized_is_not_imported_again_whatever_period_operations_now_labels_it():
    first, _e, *_ = _prepare([IN_P12], window=P12)
    known = receipt_register_counts(register_entries(first, "FY2026-P12"))
    for label in ("P.12 FYE 2026", "P.13 FYE 2026", "P.5 FYE 2026", None):
        relabelled = _log_row("Preforms", datetime(2026, 8, 4), 1_000, 50.0, period=label, invoice="Inv#1", po="1")
        valid, errors, _w, debug = _prepare([relabelled], window=P13, period=13, known=known)
        assert valid.empty and errors == [], label
        assert any("already in the receipt register" in n for n in debug["notes"])


def test_a_lookalike_with_a_different_date_is_flagged_not_silently_dropped_or_double_counted():
    first, *_ = _prepare([IN_P12])
    known = receipt_register_counts(register_entries(first, "FY2026-P12"))
    moved = _log_row("Preforms", datetime(2026, 8, 6), 1_000, 50.0, invoice="Inv#1", po="1")
    valid, _e, warnings, _d = _prepare([moved], known=known)
    assert len(valid) == 1                                                   # no unsupported assumption: recognized
    assert any("different delivery date" in w for w in warnings)


# --- legitimate late receipts, exactly once --------------------------------------------------------------------

def test_a_legitimate_late_receipt_is_recognized_once_across_periods():
    p12, errors, _w, debug = _prepare([IN_P12, LATE_AFTER_CUTOFF], window=EARLY_WINDOW, cutoff=date(2026, 7, 15), cutoff_period=11)
    assert errors == [] and len(p12) == 2 and len(debug["late_rows"]) == 1
    known = receipt_register_counts(register_entries(p12, "FY2026-P12"))
    p13, errors, *_ = _prepare([IN_P12, LATE_AFTER_CUTOFF], window=P13, period=13, known=known, cutoff=date(2026, 7, 15), cutoff_period=11)
    assert errors == [] and p13.empty


def test_the_july_31_csi_caps_receipt_labelled_p12_is_recognized_in_p12_once():
    csi = _log_row("CLOSURES", datetime(2026, 7, 31), 9_000_000, 65_520.0, invoice="Inv#2", po="2", vendor="CSI")
    valid, errors, _w, debug = _prepare([IN_P12, csi], layers={})
    assert errors == [] and 9_000_000 in valid["QUANTITY"].tolist() and len(debug["late_rows"]) == 1


# --- future receipts stay pending; unclear evidence keeps the one-time review ------------------------------------

def test_a_future_period_receipt_is_not_recognized_early_even_when_labelled_for_this_period():
    valid, errors, warnings, _d = _prepare([IN_P12, FUTURE, _log_row("Preforms", datetime(2026, 9, 12), 9, 9.0, period="P.12 FYE 2026", po="F", invoice="Inv#F")])
    assert valid["QUANTITY"].tolist() == [1_000] and errors == []
    assert any("labelled for period 12 but dated outside" in w for w in warnings)


def test_a_historical_receipt_the_opening_layers_cannot_settle_still_needs_the_one_time_review():
    from .conftest import layer
    row = _log_row("CLOSURES", datetime(2026, 7, 27), 9_000_000, 65_000.0, invoice="Inv#X", po="X", vendor="CSI")
    layers = {4: [layer("2026-07-27", 3_000, 20.0)]}                         # same day, other cost and quantity
    valid, errors, _w, debug = _prepare([IN_P12, row], layers=layers)
    assert len(debug["review_rows"]) == 1 and any("Confirm each one once" in e for e in errors)
    assert valid["QUANTITY"].tolist() == [1_000]
