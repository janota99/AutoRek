"""Fiscal calendar setup: generated dates, five-week Period 13, pasted dates, and errors that name the dates in conflict."""
import json
from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

from shared.fiscal_calendar import parse_period_end_dates, schedule_problems, suggest_period_ends

ROOT = Path(__file__).resolve().parents[2]
FY2026_ENDS = [date(2025, 10, 25), date(2025, 11, 22), date(2025, 12, 20), date(2026, 1, 17), date(2026, 2, 14),
               date(2026, 3, 14), date(2026, 4, 11), date(2026, 5, 9), date(2026, 6, 6), date(2026, 7, 4),
               date(2026, 8, 1), date(2026, 8, 29), date(2026, 10, 3)]


def test_four_or_five_week_period_13_is_generated_from_the_start_date():
    four = suggest_period_ends(date(2025, 9, 28))
    five = suggest_period_ends(date(2025, 9, 28), 35)
    assert four[:12] == five[:12] == tuple(FY2026_ENDS[:12])
    assert four[12] == date(2026, 9, 26) and five[12] == date(2026, 10, 3)


def test_the_reported_schedule_is_valid_and_a_real_conflict_names_both_dates():
    assert schedule_problems(date(2025, 9, 28), FY2026_ENDS) == []
    broken = list(FY2026_ENDS)
    broken[2] = date(2025, 11, 22)                                 # Period 03 not after Period 02
    (problem,) = schedule_problems(date(2025, 9, 28), broken)[:1]
    assert "Period 03 ends 2025-11-22" in problem and "Period 02's end (2025-11-22)" in problem
    several = list(FY2026_ENDS)
    several[1], several[5] = date(2025, 10, 1), date(2025, 12, 1)
    assert len(schedule_problems(date(2025, 9, 28), several)) >= 2     # every conflict is listed, not just the first
    assert "before it starts" in schedule_problems(date(2025, 9, 28), [date(2025, 9, 1)] + FY2026_ENDS[1:])[0]
    assert "needs 13" in schedule_problems(date(2025, 9, 28), FY2026_ENDS[:12])[0]


def test_pasted_dates_accept_common_forms_and_name_the_bad_entry():
    text = "\n".join(d.strftime("%m/%d/%Y") for d in FY2026_ENDS)
    assert parse_period_end_dates(text) == (tuple(FY2026_ENDS), None)
    assert parse_period_end_dates(", ".join(d.isoformat() for d in FY2026_ENDS))[0] == tuple(FY2026_ENDS)
    assert parse_period_end_dates("\t".join(d.strftime("%m/%d/%y") for d in FY2026_ENDS))[0] == tuple(FY2026_ENDS)
    dates, problem = parse_period_end_dates("2025-10-25\nNov 22\n")
    assert dates is None and "Entry 2 ('Nov 22')" in problem
    assert parse_period_end_dates("2025-10-25\n2025-11-22")[1].startswith("Found 2 dates")
    assert parse_period_end_dates("13/45/2025")[0] is None


def _page(monkeypatch, tmp_path):
    monkeypatch.setenv("FISCAL_SETTINGS_PATH", str(tmp_path / "fiscal.json"))
    return AppTest.from_file(str(ROOT / "apps" / "dashboard.py"), default_timeout=60).run()


def _save(at):
    next(b for b in at.button if b.label == "Save calendar").click()
    return at.run()


def test_year_and_start_generate_the_dates_and_a_five_week_period_13_saves(monkeypatch, tmp_path):
    at = _page(monkeypatch, tmp_path)
    at.number_input(key="pp-fiscal-year-0").set_value(2026)
    at.date_input(key="pp-fiscal-start-0").set_value(date(2025, 9, 28))
    at.run()
    assert [at.date_input(key=f"pp-fiscal-end-{n}-0").value for n in range(1, 14)][:12] == FY2026_ENDS[:12]
    assert at.date_input(key="pp-fiscal-end-13-0").value == date(2026, 9, 26)          # four weeks by default
    at.radio(key="pp-fiscal-p13-0").set_value("5 weeks")
    at.run()
    assert at.date_input(key="pp-fiscal-end-13-0").value == date(2026, 10, 3)
    assert at.date_input(key="pp-fiscal-end-3-0").value == date(2025, 12, 20)         # other dates untouched
    at = _save(at)
    saved = json.loads((tmp_path / "fiscal.json").read_text(encoding="utf-8"))
    assert saved["year"] == 2026 and saved["period_ends"] == [d.isoformat() for d in FY2026_ENDS] and not at.exception


def test_what_is_shown_is_what_is_validated_and_a_conflict_names_the_cells_and_disables_save(monkeypatch, tmp_path):
    at = _page(monkeypatch, tmp_path)
    at.number_input(key="pp-fiscal-year-0").set_value(2026)
    at.date_input(key="pp-fiscal-start-0").set_value(date(2025, 9, 28))
    at.run()
    at.date_input(key="pp-fiscal-end-3-0").set_value(date(2025, 11, 1))
    at.run()
    messages = " ".join(e.value for e in at.error)
    assert "Period 03 ends 2025-11-01" in messages and "Period 02's end (2025-11-22)" in messages
    assert next(b for b in at.button if b.label == "Save calendar").disabled
    assert not (tmp_path / "fiscal.json").exists()
    at.date_input(key="pp-fiscal-end-3-0").set_value(date(2025, 12, 20))               # fix it: the error clears
    at.run()
    assert not any("Period 03" in e.value for e in at.error)
    assert not next(b for b in at.button if b.label == "Save calendar").disabled


def test_pasting_all_thirteen_dates_fills_the_boxes_and_a_bad_paste_says_why(monkeypatch, tmp_path):
    at = _page(monkeypatch, tmp_path)
    at.number_input(key="pp-fiscal-year-0").set_value(2026)
    at.date_input(key="pp-fiscal-start-0").set_value(date(2025, 9, 28))
    at.run()
    at.text_area(key="pp-fiscal-paste-0").set_value("\n".join(d.strftime("%m/%d/%Y") for d in FY2026_ENDS))
    at.run()
    next(b for b in at.button if b.label == "Use these dates").click()
    at.run()
    assert [at.date_input(key=f"pp-fiscal-end-{n}-0").value for n in range(1, 14)] == FY2026_ENDS
    at.text_area(key="pp-fiscal-paste-0").set_value("2025-10-25\nsoon")
    at.run()
    next(b for b in at.button if b.label == "Use these dates").click()
    at.run()
    assert any("Entry 2 ('soon')" in e.value for e in at.error)
    assert [at.date_input(key=f"pp-fiscal-end-{n}-0").value for n in range(1, 14)] == FY2026_ENDS   # nothing half-applied


def test_a_second_year_leaves_the_first_and_a_saved_year_comes_back_with_its_five_week_period(monkeypatch, tmp_path):
    at = _page(monkeypatch, tmp_path)
    at.number_input(key="pp-fiscal-year-0").set_value(2026)
    at.date_input(key="pp-fiscal-start-0").set_value(date(2025, 9, 28))
    at.run()
    at.radio(key="pp-fiscal-p13-0").set_value("5 weeks")
    at.run()
    at = _save(at)
    at.number_input(key="pp-fiscal-year-1").set_value(2027)
    at.date_input(key="pp-fiscal-start-1").set_value(date(2026, 10, 4))
    at.run()
    assert at.date_input(key="pp-fiscal-end-13-1").value == date(2027, 10, 2)
    at = _save(at)
    saved = json.loads((tmp_path / "fiscal.json").read_text(encoding="utf-8"))
    assert set(saved["calendars"]) == {"2026", "2027"}
    assert saved["calendars"]["2026"]["period_ends"][12] == "2026-10-03"               # FY2026 kept its five weeks
    again = AppTest.from_file(str(ROOT / "apps" / "dashboard.py"), default_timeout=60).run()
    again.number_input(key="pp-fiscal-year-0").set_value(2026)
    again.date_input(key="pp-fiscal-start-0").set_value(date(2025, 9, 28))
    again.run()
    assert again.date_input(key="pp-fiscal-end-13-0").value == date(2026, 10, 3)
    assert again.radio(key="pp-fiscal-p13-0").value == "5 weeks"
