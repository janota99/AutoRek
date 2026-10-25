"""The reconciliation controls must be able to fail, and must compare in whole cents.

Each test rebuilds the controls table from a finished result with one thing deliberately wrong, then checks that
the control which watches that thing reports FAIL. A control that restates a figure from the list that produced it
would pass all of these; that is the failure these tests exist to catch.
"""

import pandas as pd
import pytest

from apps.recon.duplicates import AMOUNT_CENTS
from apps.recon.matching import (
    DISPOSITION_MATCHED,
    DISPOSITION_REVIEW_HOLD,
    DISPOSITION_TRUE_UNMATCHED,
)
from apps.recon.matching.summaries import build_controls
from apps.recon.tests.test_dispositions import mixed_result  # noqa: F401  (the shared fixture)


def _controls(result, **overrides):
    args = dict(
        qb=result.qb_work, inf=result.inf_work, matches=result.matches,
        historical_clearances=result.historical_clearances,
        unmatched_qb=result.unmatched_qb, unmatched_inf=result.unmatched_inf,
        duplicate_qb_rows=result.duplicate_qb_rows, duplicate_inf_rows=result.duplicate_inf_rows,
        duplicate_review_hold_qb_rows=result.duplicate_review_hold_qb_rows,
        duplicate_review_hold_inf_rows=result.duplicate_review_hold_inf_rows,
        amount_variance_review_hold_qb_rows=result.amount_variance_review_hold_qb_rows,
        amount_variance_review_hold_inf_rows=result.amount_variance_review_hold_inf_rows,
        ambiguous_duplicate_qb_rows=result.ambiguous_duplicate_qb_rows,
        fuzzy_match_review_hold_qb_rows=result.fuzzy_match_review_hold_qb_rows,
        fuzzy_match_review_hold_inf_rows=result.fuzzy_match_review_hold_inf_rows,
        reference_hold_qb_rows=result.reference_hold_qb_rows, qb_dispositions=result.qb_dispositions,
    )
    args.update(overrides)
    return build_controls(**args).set_index("Check")


def _failed(controls) -> set[str]:
    return set(controls.index[controls["Status"] != "PASS"])


def test_the_untouched_result_passes_every_control(mixed_result):  # noqa: F811
    assert not _failed(_controls(mixed_result))


def test_a_held_row_the_ledger_calls_matched_fails_its_hold_control(mixed_result):  # noqa: F811
    ledger = mixed_result.qb_dispositions.copy()
    held = ledger["Final Disposition"] == DISPOSITION_REVIEW_HOLD
    assert held.any(), "the fixture must contain a review hold"
    ledger.loc[held, "Final Disposition"] = DISPOSITION_MATCHED
    failed = _failed(_controls(mixed_result, qb_dispositions=ledger))
    assert any("review hold" in name.lower() or "excluded from" in name.lower() for name in failed), failed


def test_an_excluded_duplicate_the_ledger_calls_unmatched_fails_the_duplicate_control(mixed_result):  # noqa: F811
    ledger = mixed_result.qb_dispositions.copy()
    ledger.loc[ledger["QBO Row ID"] == "QB-2", "Final Disposition"] = DISPOSITION_TRUE_UNMATCHED
    failed = _failed(_controls(mixed_result, qb_dispositions=ledger))
    assert "Excess QuickBooks copies excluded from JE" in failed
    assert "Unresolved JE support" in failed


def test_one_cent_off_in_the_ledger_is_reported_with_its_exact_difference(mixed_result):  # noqa: F811
    ledger = mixed_result.qb_dispositions.copy()
    row = ledger["Final Disposition"] == DISPOSITION_TRUE_UNMATCHED
    ledger.loc[row, "Amount"] = ledger.loc[row, "Amount"] + 0.01
    controls = _controls(mixed_result, qb_dispositions=ledger)
    assert controls.loc["Unresolved JE support", "Status"] == "FAIL"
    assert controls.loc["Unresolved JE support", "Difference"] == pytest.approx(-0.01 * row.sum())


def test_an_infinium_exception_the_buckets_do_not_explain_fails(mixed_result):  # noqa: F811
    matched_inf = sorted({int(i) for group in mixed_result.matches for i in group.inf_rows})
    assert matched_inf
    controls = _controls(mixed_result, unmatched_inf=[matched_inf[0]])
    assert controls.loc["Infinium-only exceptions reported separately (no QuickBooks JE effect)", "Status"] == "FAIL"


def test_a_row_dropped_from_every_bucket_fails_the_roll_forwards(mixed_result):  # noqa: F811
    controls = _controls(mixed_result, unmatched_qb=list(mixed_result.unmatched_qb)[1:] if len(mixed_result.unmatched_qb) > 1
                         else [], qb_dispositions=None)
    assert {"QuickBooks row completeness", "QuickBooks amount roll-forward"} <= _failed(controls)


def test_status_is_decided_on_whole_cents_not_on_floats(mixed_result):  # noqa: F811
    qb = mixed_result.qb_work.copy()
    qb.loc[qb.index[0], AMOUNT_CENTS] = int(qb.loc[qb.index[0], AMOUNT_CENTS]) + 1
    controls = _controls(mixed_result, qb=qb)
    failed = controls[controls["Status"] != "PASS"]
    assert not failed.empty and (failed["Difference"].abs() >= 0.0099).all()
    assert controls["Difference"].map(lambda v: isinstance(v, float)).all()
