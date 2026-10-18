"""Fiscal calendar logic and the workspace -> tool hand-off."""
from datetime import date

from shared.fiscal_calendar import default_calendar, locate, resolve, validate


def test_default_calendar_is_13_four_week_periods():
    cal = default_calendar(2026)
    assert validate(cal) is None
    assert cal.period_ends[0] == date(2026, 1, 28)
    assert cal.period_ends[-1] == date(2026, 12, 30)


def test_locate_and_resolve():
    cal = default_calendar(2026)
    assert locate(cal, date(2026, 1, 1)) == 1
    assert locate(cal, date(2026, 1, 29)) == 2
    assert locate(cal, date(2026, 12, 31)) is None
    assert locate(cal, date(2025, 12, 31)) is None
    assert resolve(cal, None) is None
    ctx = resolve(cal, date(2026, 7, 15))
    assert (ctx.fiscal_year, ctx.period) == (2026, 7)
    assert resolve(cal, date(2030, 1, 1)).period is None


def test_validate_rejects_out_of_order_ends():
    cal = default_calendar(2026)
    ends = list(cal.period_ends)
    ends[3] = ends[1]
    bad = type(cal)(cal.year, cal.start, tuple(ends))
    assert "Period 04" in validate(bad)
    assert locate(bad, date(2026, 2, 1)) is None


def test_pending_hands_off_once_per_change(tmp_path, monkeypatch):
    monkeypatch.setenv("FISCAL_SETTINGS_PATH", str(tmp_path / "fiscal.json"))
    from streamlit.testing.v1 import AppTest

    def script():
        import streamlit as st
        from shared import fiscal_session
        from datetime import date
        if fiscal_session.get_end_date() is None:
            fiscal_session.set_end_date(date(2026, 3, 1))
        st.session_state.setdefault("log", []).append(
            (fiscal_session.pending_for("fifo") is not None, fiscal_session.pending_for("fifo") is not None))
        if st.button("change"):
            fiscal_session.set_end_date(date(2026, 4, 1))

    at = AppTest.from_function(script).run()
    assert at.session_state["log"][-1] == (True, False)
    at.run()
    assert at.session_state["log"][-1] == (False, False)
    at.button[0].click().run()   # the change is made after this run's hand-off, so it lands on the next run
    at.run()
    assert at.session_state["log"][-1] == (True, False)


def test_settings_persist_across_sessions(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    path = tmp_path / "fiscal.json"
    monkeypatch.setenv("FISCAL_SETTINGS_PATH", str(path))

    def script():
        import streamlit as st
        from datetime import date
        from shared import fiscal_session
        if st.button("set"):
            fiscal_session.set_end_date(date(2026, 7, 15))
        st.write(str(fiscal_session.get_end_date()))
        st.write(str(fiscal_session.get_calendar().year))

    first = AppTest.from_function(script).run()
    first.button[0].click().run()
    assert path.exists()
    second = AppTest.from_function(script).run()          # a brand-new session
    assert second.markdown[0].value == "2026-07-15"


def test_corrupt_settings_file_falls_back_to_defaults(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    path = tmp_path / "fiscal.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("FISCAL_SETTINGS_PATH", str(path))

    def script():
        import streamlit as st
        from shared import fiscal_session
        st.write(str(fiscal_session.get_end_date()))

    at = AppTest.from_function(script).run()
    assert not at.exception and at.markdown[0].value == "None"
