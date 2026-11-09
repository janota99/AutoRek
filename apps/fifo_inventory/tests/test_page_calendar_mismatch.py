"""The Workspace calendar is the authority: a workbook disagreement only warns; a missing or unsaved calendar blocks."""
import io
from datetime import datetime

from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

from apps.fifo_inventory import materials
from shared.fiscal_calendar import default_calendar

from .test_workbook_import import CALENDAR, COUNTS, GOOD_ROWS, _workbook


def _workbook_with_real_dates():
    """The fixture workbook with row 1 of PERIOD END 2026 carrying FY2026's real period-end dates."""
    wb = load_workbook(io.BytesIO(_workbook(GOOD_ROWS, COUNTS)))
    sheet = wb["PERIOD END 2026"]
    for column, end in enumerate(CALENDAR.period_ends, start=5):
        sheet.cell(row=1, column=column, value=datetime(end.year, end.month, end.day))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _run(monkeypatch, tmp_path, snapshot_dir, calendar, verified=True):
    """Drive the real FIFO page with the workbook 'uploaded' and the given calendar saved in Workspace."""
    monkeypatch.setenv("FISCAL_SETTINGS_PATH", str(tmp_path / "fiscal.json"))
    monkeypatch.setattr(materials, "ALIASES_PATH", tmp_path / "aliases.json")
    data = _workbook_with_real_dates()

    class Upload(io.BytesIO):
        name = "Raw Goods & Receivings.xlsx"

        def getvalue(self):
            return data

    import streamlit as st
    monkeypatch.setattr(st, "file_uploader", lambda label, *a, key=None, **k: Upload(data) if key == "ops_workbook_upload" else None)

    at = AppTest.from_file("apps/fifo_inventory/app.py", default_timeout=120)
    at.session_state["suite_fiscal_loaded"] = True
    at.session_state["suite_fiscal_calendar"] = calendar
    at.session_state["suite_fiscal_calendars"] = {calendar.year: calendar}
    at.session_state["suite_fiscal_end"] = None
    at.session_state["suite_fiscal_verified"] = {calendar.year} if verified else set()
    at.session_state["fifo_fiscal_year"] = 2026
    at.session_state["fifo_fiscal_period"] = 12
    return at.run()


def _markdown(at):
    return " ".join(m.value for m in at.markdown) + " " + " ".join(e.value for e in at.error)


def test_a_workspace_calendar_that_disagrees_with_the_workbook_warns_with_both_dates_and_does_not_block(monkeypatch, tmp_path, snapshot_dir):
    saved = default_calendar(2026)                                    # a saved calendar that differs from the workbook's dates
    at = _run(monkeypatch, tmp_path, snapshot_dir, saved)
    assert not at.exception
    warnings = " ".join(w.value for w in at.warning)
    assert "Aug 29, 2026" in warnings                                  # the workbook's period-end date
    assert f"{saved.period_ends[11]:%b %d, %Y}" in warnings            # the Workspace calendar's date
    assert "Workspace" in warnings
    assert "Fiscal Period Mismatch" not in _markdown(at) and "Fiscal Calendar Required" not in _markdown(at)
    assert not at.exception


def test_a_missing_calendar_blocks_and_no_default_is_substituted(monkeypatch, tmp_path, snapshot_dir):
    at = _run(monkeypatch, tmp_path, snapshot_dir, default_calendar(2026), verified=False)   # the unsaved placeholder
    assert not at.exception
    text = _markdown(at)
    assert "Fiscal Calendar Required" in text and "No saved fiscal calendar exists for FY2026" in text
    assert "Open Workspace to update the fiscal calendar" in text
    assert not any(b.label.startswith("Close ") for b in at.button)
    assert not any("workpaper" in b.label.lower() for b in at.button)
    assert "ending count(s) supplied" not in text + " ".join(c.value for c in at.caption)


def test_the_message_does_not_appear_when_the_calendar_is_saved_and_dates_match(monkeypatch, tmp_path, snapshot_dir):
    at = _run(monkeypatch, tmp_path, snapshot_dir, CALENDAR)
    assert not at.exception
    assert "Fiscal Calendar Required" not in _markdown(at)
    assert not any("differs" in w.value or "says Period" in w.value for w in at.warning)
    assert any(b.label.startswith("Close ") for b in at.button)        # the normal flow is available


def test_a_year_typo_in_the_workbook_alone_does_not_trigger_the_message(monkeypatch, tmp_path, snapshot_dir):
    # Period 1 typo (a year off) while the calendar is right: selecting P1 notes it and carries on.
    monkeypatch.setenv("FISCAL_SETTINGS_PATH", str(tmp_path / "fiscal.json"))
    from apps.fifo_inventory.workbook_import import read_period_end
    from apps.fifo_inventory.intake import period_window
    from apps.fifo_inventory.materials import MaterialMatcher
    from apps.fifo_inventory.user_inputs import PRODUCTS
    wb = load_workbook(io.BytesIO(_workbook_with_real_dates()))
    wb["PERIOD END 2026"].cell(row=1, column=5, value=datetime(2026, 10, 25))
    out = io.BytesIO()
    wb.save(out)
    report = read_period_end(out.getvalue(), "PERIOD END 2026", MaterialMatcher(PRODUCTS, {}), 1,
                             period_window(2026, 1, CALENDAR, CALENDAR.period_ends[0]), calendar=CALENDAR)
    assert report.calendar_mismatch is None and report.errors == []
