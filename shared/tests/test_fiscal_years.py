"""Every fiscal year's calendar is kept: saving several, retrieving earlier ones, and the older one-year file."""
import json
from datetime import date

import pytest

from shared import fiscal_session
from shared.fiscal_calendar import FiscalCalendar, default_calendar, locate, validate

FY2026_ENDS = ("2025-10-25", "2025-11-22", "2025-12-20", "2026-01-17", "2026-02-14", "2026-03-14", "2026-04-11",
               "2026-05-09", "2026-06-06", "2026-07-04", "2026-08-01", "2026-08-29", "2026-10-03")      # Period 13 runs 5 weeks
FY2027_ENDS = ("2026-10-31", "2026-11-28", "2026-12-26", "2027-01-23", "2027-02-20", "2027-03-20", "2027-04-17",
               "2027-05-15", "2027-06-12", "2027-07-10", "2027-08-07", "2027-09-04", "2027-10-02")
FY2026 = FiscalCalendar(2026, date(2025, 9, 28), tuple(date.fromisoformat(d) for d in FY2026_ENDS))
FY2027 = FiscalCalendar(2027, date(2026, 10, 4), tuple(date.fromisoformat(d) for d in FY2027_ENDS))


@pytest.fixture
def session(tmp_path, monkeypatch):
    """A fresh 'server session' over a scratch settings file. Call ``restart()`` for a new session on the same file."""
    path = tmp_path / "fiscal.json"
    monkeypatch.setenv("FISCAL_SETTINGS_PATH", str(path))
    state = {}
    monkeypatch.setattr(fiscal_session.st, "session_state", state)

    class Handle:
        file = path

        @staticmethod
        def restart():
            state.clear()

        @staticmethod
        def saved():
            return json.loads(path.read_text(encoding="utf-8"))
    return Handle


def test_the_two_calendars_are_valid_and_fy2026_keeps_its_five_week_period_13():
    assert validate(FY2026) is None and validate(FY2027) is None
    assert (FY2026.period_ends[12] - FY2026.period_ends[11]).days == 35


def test_saving_a_second_year_keeps_the_first(session):
    fiscal_session.set_calendar(FY2026)
    fiscal_session.set_calendar(FY2027)
    assert fiscal_session.saved_years() == [2026, 2027]
    assert fiscal_session.calendar_for(2026) == FY2026 and fiscal_session.calendar_for(2027) == FY2027
    assert fiscal_session.get_calendar() == FY2027                               # the one saved last is the one shown
    assert fiscal_session.calendar_for(2026).period_ends[12] == date(2026, 10, 3)  # the five-week Period 13 is intact


def test_every_year_survives_a_restart_and_the_active_one_is_restored(session):
    fiscal_session.set_calendar(FY2026)
    fiscal_session.set_calendar(FY2027)
    session.restart()
    assert fiscal_session.saved_years() == [2026, 2027]
    assert fiscal_session.get_calendar() == FY2027
    assert fiscal_session.calendar_for(2026) == FY2026


def test_an_earlier_year_can_be_retrieved_and_made_active_without_losing_the_other(session):
    fiscal_session.set_calendar(FY2026)
    fiscal_session.set_calendar(FY2027)
    assert fiscal_session.use_calendar(2026) is True
    assert fiscal_session.get_calendar() == FY2026 and fiscal_session.calendar_for(2027) == FY2027
    session.restart()
    assert fiscal_session.get_calendar() == FY2026                               # the choice is remembered
    assert fiscal_session.use_calendar(2030) is False and fiscal_session.get_calendar() == FY2026


def test_a_tool_gets_the_calendar_of_the_year_it_is_processing(session):
    fiscal_session.set_calendar(FY2026)
    fiscal_session.set_calendar(FY2027)                                          # FY2027 is active
    assert fiscal_session.calendar_for_year(2026) == FY2026                      # FIFO processing FY2026 P12 still finds it
    assert locate(fiscal_session.calendar_for_year(2026), date(2026, 8, 29)) == 12
    assert fiscal_session.calendar_for_year(2027) == FY2027
    assert fiscal_session.calendar_for_year(2031) == FY2027                      # unsaved year: the active one, which a tool sees as not covering it


def test_resaving_a_year_replaces_only_that_year(session):
    fiscal_session.set_calendar(FY2026)
    fiscal_session.set_calendar(FY2027)
    shifted = FiscalCalendar(2027, date(2026, 10, 4), tuple(FY2027.period_ends[:12]) + (date(2027, 10, 9),))
    fiscal_session.set_calendar(shifted)
    assert fiscal_session.calendar_for(2027) == shifted and fiscal_session.calendar_for(2026) == FY2026


def test_the_file_keeps_the_older_single_year_shape_at_the_top_level(session):
    fiscal_session.set_calendar(FY2026)
    fiscal_session.set_calendar(FY2027)
    data = session.saved()
    assert data["year"] == 2027 and data["start"] == "2026-10-04" and data["period_ends"] == list(FY2027_ENDS)  # what older code reads
    assert set(data["calendars"]) == {"2026", "2027"}
    assert data["calendars"]["2026"]["period_ends"] == list(FY2026_ENDS)


def test_an_existing_single_year_file_loads_and_keeps_working(session):
    session.file.write_text(json.dumps({"year": 2026, "start": "2025-09-28", "period_ends": list(FY2026_ENDS),
                                        "end_date": "2026-08-29"}), encoding="utf-8")
    assert fiscal_session.get_calendar() == FY2026 and fiscal_session.saved_years() == [2026]
    assert fiscal_session.calendar_for(2026) == FY2026 and fiscal_session.get_end_date() == date(2026, 8, 29)
    fiscal_session.set_calendar(FY2027)                                          # adding a year keeps the old one
    assert fiscal_session.saved_years() == [2026, 2027]
    assert session.saved()["end_date"] == "2026-08-29"                           # the optional period end date is untouched
    session.restart()
    assert fiscal_session.calendar_for(2026) == FY2026 and fiscal_session.get_calendar() == FY2027


def test_a_damaged_year_is_skipped_and_the_rest_are_kept(session):
    data = {"year": 2027, "start": "2026-10-04", "period_ends": list(FY2027_ENDS), "end_date": None,
            "calendars": {"2026": {"start": "2025-09-28", "period_ends": list(FY2026_ENDS)},
                          "2025": {"start": "nonsense", "period_ends": ["x"]},
                          "2024": {"start": "2023-10-01", "period_ends": ["2023-10-28"]}}}   # too few periods
    session.file.write_text(json.dumps(data), encoding="utf-8")
    assert fiscal_session.saved_years() == [2026, 2027]


def test_a_missing_or_corrupt_file_falls_back_to_the_default_calendar(session):
    assert fiscal_session.saved_years() == [fiscal_session.get_calendar().year]
    session.restart()
    session.file.write_text("{not json", encoding="utf-8")
    assert fiscal_session.get_calendar() == default_calendar(date.today().year)


def test_a_date_from_another_saved_year_still_resolves_to_its_period(session):
    fiscal_session.set_calendar(FY2026)
    fiscal_session.set_calendar(FY2027)                                          # FY2027 active
    fiscal_session.set_end_date(date(2026, 8, 29))
    context = fiscal_session.current()
    assert (context.fiscal_year, context.period) == (2026, 12)
    fiscal_session.set_end_date(date(2027, 3, 20))
    assert (fiscal_session.current().fiscal_year, fiscal_session.current().period) == (2027, 6)
    fiscal_session.set_end_date(date(2031, 1, 1))
    assert fiscal_session.current().period is None                               # outside every saved year


def test_returning_to_a_saved_year_does_not_rebuild_it_from_defaults(session):
    fiscal_session.set_calendar(FY2026)
    fiscal_session.set_calendar(FY2027)
    # The workspace form: year and start typed back to FY2026 with the table left as it was (FY2027's)
    result = fiscal_session.calendar_from_edit(2026, date(2025, 9, 28), FY2027.period_ends)
    assert result == FY2026 and result.period_ends[12] == date(2026, 10, 3)      # not four-week defaults
    fresh = fiscal_session.calendar_from_edit(2028, date(2027, 10, 3), FY2027.period_ends)
    assert fresh == default_calendar(2028, date(2027, 10, 3))                    # an unsaved year is built from its start
    typed = tuple(FY2027.period_ends)
    assert fiscal_session.calendar_from_edit(2027, date(2026, 10, 4), typed) == FiscalCalendar(2027, date(2026, 10, 4), typed)
