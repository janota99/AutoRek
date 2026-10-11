"""The period page's step, blockers, and close gate. Display wording only: the gate itself lives in app.py,
and ``blocks_close`` is its restated twin, checked here against every combination of the conditions."""
import itertools

import pytest

from apps.fifo_inventory.period_status import CLOSE, PREVIEW, UPLOAD, VALIDATE, blocks_close, period_status

KEY = "FY2026-P03"


def _preview(**kw):
    base = {"stale": False, "changed": [], "fail": 0, "review": 0, "warnings": 0,
            "processing_errors": 0, "receipt_errors": 0, "review_ack": False, "demo": False}
    return {**base, **kw}


def _status(preview=None, **kw):
    args = {"period_key": KEY, "has_grid_data": True, "input_errors": [], "sequence_error": None,
            "already_closed": False}
    return period_status(**{**args, **kw}, preview=preview)


def test_nothing_loaded_asks_for_the_ending_inventory():
    s = _status(has_grid_data=False)
    assert s["step"] == UPLOAD and s["ready"] == "" and "ending inventory" in s["blockers"][0]


def test_input_problems_are_listed_and_capped():
    s = _status(has_grid_data=True, input_errors=[f"problem {n}" for n in range(5)])
    assert s["step"] == VALIDATE
    assert s["blockers"][:3] == ["problem 0", "problem 1", "problem 2"]
    assert "2 more" in s["blockers"][3] and s["details"][1] == "5 to fix"


def test_a_sequence_error_blocks_even_with_clean_inputs():
    s = _status(sequence_error="The next allowable period is FY2026-P04.")
    assert s["blockers"] == ["The next allowable period is FY2026-P04."]


def test_clean_inputs_without_a_preview_are_ready_to_calculate():
    s = _status()
    assert s["step"] == PREVIEW and s["blockers"] == [] and s["ready"] == "Ready to calculate a preview."


def test_a_closed_period_is_done_and_points_to_reopening():
    s = _status(already_closed=True, input_errors=["ignored once closed"])
    assert s["step"] == CLOSE and s["blockers"] == [] and "History & Snapshots" in s["next_step"]


def test_a_passing_preview_is_ready_to_close():
    s = _status(_preview())
    assert s["step"] == CLOSE and s["blockers"] == [] and s["ready"] == "Ready to close FY2026-P03."


def test_failures_block_with_a_fail_tone_and_review_items_need_acknowledging():
    failed = _status(_preview(fail=2))
    assert failed["tone"] == "fail" and "2 product(s) FAIL" in failed["blockers"][0] and failed["step"] == PREVIEW
    review = _status(_preview(review=1))
    assert review["tone"] == "blocked" and "Acknowledge" in review["blockers"][0]
    assert _status(_preview(review=1, review_ack=True))["blockers"] == []
    assert "Acknowledge" in _status(_preview(warnings=2))["blockers"][0]


def test_a_stale_preview_names_what_changed():
    s = _status(_preview(stale=True, changed=["Ending inventory grid", "Receipts"]))
    assert "(Ending inventory grid, Receipts)" in s["blockers"][0]
    assert s["details"][2] == "Out of date" and s["details"][3] == "Blocked"


def test_demo_data_blocks_closing():
    assert "Demo data is loaded" in _status(_preview(demo=True))["blockers"][0]


def test_every_blocker_is_reported_together():
    s = _status(_preview(stale=True, fail=1, processing_errors=1, receipt_errors=1, review=1, demo=True))
    assert len(s["blockers"]) == 6


FLAGS = ("stale", "already_closed", "has_sequence_error", "has_input_errors", "has_processing_errors",
         "has_fail", "needs_ack_unacked", "demo")


@pytest.mark.parametrize("combo", list(itertools.product([False, True], repeat=len(FLAGS))))
def test_the_status_agrees_with_the_close_gate_in_every_combination(combo):
    f = dict(zip(FLAGS, combo))
    gate = blocks_close(
        stale=f["stale"], already_closed=f["already_closed"],
        sequence_error="out of order" if f["has_sequence_error"] else None,
        input_errors=1 if f["has_input_errors"] else 0, processing_errors=1 if f["has_processing_errors"] else 0,
        fail=1 if f["has_fail"] else 0, review_needs_ack=f["needs_ack_unacked"], review_ack=False, demo=f["demo"])
    status = _status(
        _preview(stale=f["stale"], fail=int(f["has_fail"]), processing_errors=int(f["has_processing_errors"]),
                 receipt_errors=int(f["has_input_errors"]), review=int(f["needs_ack_unacked"]), demo=f["demo"]),
        already_closed=f["already_closed"],
        sequence_error="out of order" if f["has_sequence_error"] else None,
        input_errors=["bad input"] if f["has_input_errors"] else [])
    if f["already_closed"]:
        assert gate and status["blockers"] == []        # closed: nothing left to close, nothing to fix
    else:
        assert gate == bool(status["blockers"])
