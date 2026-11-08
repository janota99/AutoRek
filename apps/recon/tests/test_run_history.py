"""An unreachable history database must cost the page one wait, not one per rerun.

A Streamlit page re-runs on every click, and a database that does not answer costs several seconds per attempt, so the
read paths pause for a while after one failure. Nothing here touches a real database.
"""
from contextlib import contextmanager

import pytest

from apps.recon import run_history


@pytest.fixture
def dead_database(monkeypatch):
    attempts = []

    @contextmanager
    def refuse(config, *, connect_timeout):
        attempts.append(connect_timeout)
        raise ConnectionError("no answer")
        yield  # pragma: no cover

    monkeypatch.setattr(run_history, "_config", lambda: object())
    monkeypatch.setattr(run_history, "tenant_connection", refuse)
    monkeypatch.setattr(run_history, "_unreachable_until", 0.0)
    run_history.known_input_runs.clear()
    yield attempts
    run_history._note_reachable()
    run_history.known_input_runs.clear()


def test_a_failed_read_pauses_later_reads_instead_of_waiting_again(dead_database):
    assert run_history.known_input_runs(("a",)) == {}
    assert len(dead_database) == 1 and run_history._recently_unreachable()
    assert run_history.known_input_runs(("b",)) == {}      # a different key, so not served from the cache
    assert len(dead_database) == 1                          # and the database was not tried again


def test_reads_resume_once_the_pause_has_passed(dead_database, monkeypatch):
    run_history.known_input_runs(("a",))
    monkeypatch.setattr(run_history, "_unreachable_until", 0.0)
    run_history.known_input_runs(("b",))
    assert len(dead_database) == 2
