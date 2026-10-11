"""Reconciliation page wording: what blocks the next step, and what changed between runs.

Display only. These pin that a failed control, a review hold, and a cleared result are always
named, and that the run summary carries only figures the engine already produced.
"""
from apps.recon.run_status import cleared_note, result_status, summarize_run, upload_blockers
from shared.workflow import changes_since

from apps.recon.run_status import MONEY_KEYS, SUMMARY_LABELS


def _metrics(**kw):
    base = {"Control Status": "PASS", "Posting Status": "READY TO POST", "Unresolved QuickBooks Rows": 0,
            "Final Disposition - Review Hold Rows": 0, "Matched QuickBooks Rows": 10,
            "Unmatched Infinium Rows": 0, "Proposed JE Amount": 0.0, "Matched Amount Difference": 0.0}
    return {**base, **kw}


def test_each_missing_upload_is_named_independently():
    assert upload_blockers(False, False) == ["Upload the QuickBooks sales file.", "Upload the Infinium sales file."]
    assert upload_blockers(True, False) == ["Upload the Infinium sales file."]
    assert upload_blockers(True, True) == []


def test_failed_controls_withhold_downloads_and_say_how_many():
    s = result_status(_metrics(**{"Control Status": "FAIL"}), failed_controls=2, workbook_prepared=False)
    assert s["tone"] == "fail" and "2 automated control(s) failed" in s["blockers"][0]
    assert "withheld" in s["blockers"][0]


def test_a_failed_control_reported_as_zero_still_counts_as_one():
    s = result_status(_metrics(**{"Control Status": "FAIL"}), failed_controls=0, workbook_prepared=False)
    assert "1 automated control(s) failed" in s["blockers"][0]


def test_a_review_hold_blocks_posting_but_not_the_workpaper():
    s = result_status(_metrics(**{"Posting Status": "REVIEW REQUIRED", "Final Disposition - Review Hold Rows": 3}),
                      failed_controls=0, workbook_prepared=False)
    assert "3 item(s) are on review hold" in s["blockers"][0]
    assert "workpaper can still be prepared" in s["blockers"][0]


def test_unresolved_items_are_not_a_blocker_but_are_the_next_step():
    s = result_status(_metrics(**{"Unresolved QuickBooks Rows": 4}), 0, False)
    assert s["blockers"] == [] and "4 unresolved" in s["ready"] and "Exception Review" in s["next_step"]


def test_a_prepared_workpaper_points_to_the_download():
    s = result_status(_metrics(), 0, True)
    assert s["blockers"] == [] and "Downloads tab" in s["next_step"]


def test_a_fully_matched_run_goes_straight_to_the_workpaper():
    s = result_status(_metrics(), 0, False)
    assert "every row matched" in s["ready"]


def test_the_run_summary_carries_only_known_figures():
    summary = summarize_run({**_metrics(), "Secret Internal Thing": 1})
    assert "Secret Internal Thing" not in summary
    assert set(summary) <= set(SUMMARY_LABELS)


def test_the_change_lines_between_two_runs_use_cents_and_signed_deltas():
    money = {k: (lambda v: f"${v:,.2f}") for k in MONEY_KEYS}
    before = summarize_run(_metrics(**{"Matched QuickBooks Rows": 100, "Proposed JE Amount": 500.0}))
    after = summarize_run(_metrics(**{"Matched QuickBooks Rows": 108, "Proposed JE Amount": 120.5,
                                      "Control Status": "FAIL"}))
    lines = changes_since(before, after, SUMMARY_LABELS, money)
    assert "Matched QuickBooks rows: 100 → 108 (+8)" in lines
    assert "Proposed journal entry: $500.00 → $120.50 (-$379.50)" in lines
    assert "Automated controls: PASS → FAIL" in lines
    assert len(lines) == 3


def test_a_cleared_result_names_why():
    assert cleared_note(["QuickBooks file changed", "Column mappings changed"]) == (
        "The previous results were cleared: QuickBooks file changed, Column mappings changed.")
    assert cleared_note([]) == "" and cleared_note(None) == ""
