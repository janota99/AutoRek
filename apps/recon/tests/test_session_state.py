"""A new reconciliation result must discard every workbook prepared from the
old one. Before this was centralized, only the primary workpaper was
cleared, so Download Legacy Format served the previous run's file under the
new run ID."""

from pathlib import Path

import pytest

from apps.recon import utils


class _SessionState(dict):
    """Stand-in for st.session_state: a dict with attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


@pytest.fixture
def session_state(monkeypatch):
    state = _SessionState(
        input_signature=("old inputs",),
        reconciliation_result="old result",
        primary_workbook=b"old primary",
        primary_workbook_timing={"build": 0.5, "page": 2.0},
        legacy_workbook=b"old legacy",
    )
    monkeypatch.setattr(utils.st, "session_state", state)
    return state


def test_changed_inputs_clear_the_legacy_workbook_with_the_primary(session_state):
    utils.clear_results_if_signature_changed(("new inputs",))
    assert session_state == {"input_signature": ("new inputs",)}


def test_unchanged_inputs_keep_prepared_workbooks(session_state):
    before = dict(session_state)
    utils.clear_results_if_signature_changed(("old inputs",))
    assert session_state == before


def test_clear_prepared_workbooks_leaves_the_result_itself(session_state):
    utils.clear_prepared_workbooks()
    assert "legacy_workbook" not in session_state and "primary_workbook" not in session_state
    assert session_state["reconciliation_result"] == "old result"


def test_the_page_clears_workbooks_only_through_the_shared_helper():
    """The Run button and the missing-upload path both reset results; each
    must clear every prepared workbook, not a hand-picked key."""
    source = (Path(utils.__file__).parent / "app.py").read_text(encoding="utf-8-sig")
    assert '"primary_workbook"' not in source and '"legacy_workbook"' not in source
    assert source.count("clear_prepared_workbooks()") == 2
