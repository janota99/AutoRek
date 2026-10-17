"""The store's own refusals, which fire before any database work (the live rules are in db/tests/test_runs_live.py)."""
from dataclasses import replace

import pytest

pytest.importorskip("psycopg")
from apps.recon import run_store  # noqa: E402
from apps.recon.run_log import UNSIGNED  # noqa: E402
from apps.recon.tests.test_run_log import make  # noqa: E402


class NoDatabase:
    def execute(self, *a, **k):
        raise AssertionError("the store reached the database")


def test_a_tampered_record_is_not_saved():
    with pytest.raises(ValueError):
        run_store.save_run(NoDatabase(), "org", replace(make(), ran_by="Someone Else"))


@pytest.mark.parametrize("kind,detail", [("note", {"text": "   "}), ("note", {}), ("bogus", {"text": "x"})])
def test_bad_events_are_refused(kind, detail):
    with pytest.raises(ValueError):
        run_store.add_event(NoDatabase(), "org", "run", kind, UNSIGNED, detail)


def test_bad_workpapers_are_refused():
    with pytest.raises(ValueError):
        run_store.save_workpaper(NoDatabase(), "org", "run", kind="primary", filename="w.xlsx", content=b"",
                                 actor=UNSIGNED)
    with pytest.raises(ValueError):
        run_store.save_workpaper(NoDatabase(), "org", "run", kind="other", filename="w.xlsx", content=b"x",
                                 actor=UNSIGNED)
