"""The saved-run record: what is kept, that it is tamper-evident, and the exported log. No database, no Streamlit."""
import csv
import io
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from apps.recon.run_log import (
    HEADLINE_KEYS, LOG_COLUMNS, UNSIGNED, Actor, build_run_record, json_safe, log_to_csv, verify_record,
)

SHA_QB, SHA_INF, SHA_HIST = "a" * 64, "b" * 64, "c" * 64


def metadata(**over):
    base = {
        "run_id": "REC-20261017-101500-ABCD1234", "run_timestamp_dt": datetime(2026, 10, 17, 15, 15, tzinfo=timezone.utc),
        "qb_filename": "qb.xlsx", "qb_sheet_name": "Sheet1", "qb_sha256": SHA_QB,
        "inf_filename": "inf.csv", "inf_sheet_name": None, "inf_sha256": SHA_INF,
        "qb_secondary_filename": None, "qb_secondary_sha256": None,
        "inf_secondary_filename": "hist.xlsx", "inf_secondary_sheet_name": "H", "inf_secondary_sha256": SHA_HIST,
        "fiscal_year": 2026, "fiscal_period": 7,
    }
    base.update(over)
    return base


METRICS = {
    "Matched QuickBooks Rows": 412, "Unresolved QuickBooks Rows": 18, "Unmatched Infinium Rows": 3,
    "Final Disposition - Review Hold Rows": 2, "Proposed JE Amount": 1234.56, "Matched Amount Difference": 0.0,
    "Control Status": "PASS", "Posting Status": "REVIEW REQUIRED", "Posting Blockers": "2 holds",
    "Posting Authorization": "DO NOT POST", "Some Other Metric": 99,
}
CONTROLS = [{"Check": "Rows tie", "Expected": 430, "Actual": 430, "Difference": 0.0, "Status": "PASS", "Extra": "x"}]


def make(actor=UNSIGNED, **over):
    args = dict(metadata=metadata(), metrics=METRICS, controls=CONTROLS,
                mappings={"quickbooks": {"po": "PO"}, "infinium": {"po": "OHPO"}},
                app_version="2.17.0", matching_rule_version="RULES-1", actor=actor)
    args.update(over)
    return build_run_record(**args)


def test_record_keeps_the_headline_figures_and_controls_but_not_everything():
    r = make()
    assert set(r.headline) == set(HEADLINE_KEYS) - set()  # every kept key was present in METRICS
    assert "Some Other Metric" not in r.headline
    assert r.controls == [{"Check": "Rows tie", "Expected": 430, "Actual": 430, "Difference": 0.0, "Status": "PASS"}]
    assert (r.fiscal_year, r.fiscal_period, r.control_status, r.posting_status) == (2026, 7, "PASS", "REVIEW REQUIRED")


def test_inputs_are_fingerprints_only_and_missing_files_are_left_out():
    r = make()
    assert [(i.role, i.filename, i.sheet_name, i.sha256) for i in r.inputs] == [
        ("qb", "qb.xlsx", "Sheet1", SHA_QB), ("infinium", "inf.csv", None, SHA_INF),
        ("infinium_historical", "hist.xlsx", "H", SHA_HIST)]


def test_an_unsigned_run_is_never_marked_verified_even_if_the_caller_says_so():
    assert make().ran_by_verified is False
    assert make(actor=Actor("", True)).ran_by_verified is False       # no name: not verified
    signed = make(actor=Actor("pat@example.com", True))
    assert (signed.ran_by, signed.ran_by_verified) == ("pat@example.com", True)


def test_a_run_without_a_pass_fail_verdict_is_refused():
    with pytest.raises(ValueError):
        make(metrics={k: v for k, v in METRICS.items() if k != "Control Status"})


def test_the_digest_verifies_and_any_edit_breaks_it():
    r = make()
    assert verify_record(r)
    assert verify_record(make())                                       # deterministic for the same content
    assert not verify_record(replace(r, ran_by="Someone Else"))
    assert not verify_record(replace(r, headline={**r.headline, "Proposed JE Amount": 0.0}))
    assert not verify_record(replace(r, record_sha256=""))
    other_input = replace(r, inputs=[replace(r.inputs[0], sha256="d" * 64), *r.inputs[1:]])
    assert not verify_record(other_input)


def test_digest_ignores_input_order():
    r = make()
    assert verify_record(replace(r, inputs=list(reversed(r.inputs))))


def test_json_safe_handles_numpy_nan_and_dates():
    np = pytest.importorskip("numpy")
    out = json_safe({"a": np.int64(3), "b": np.float64("nan"), "c": np.float64(1.5), "d": datetime(2026, 1, 2),
                     "e": (1, 2), "f": float("inf")})
    assert out == {"a": 3, "b": None, "c": 1.5, "d": "2026-01-02T00:00:00", "e": [1, 2], "f": None}


def test_no_fiscal_period_is_stored_as_none():
    assert make(metadata=metadata(fiscal_period=None)).fiscal_period is None


def test_log_csv_has_one_row_per_run_a_bom_and_the_fingerprints():
    data = log_to_csv([make(), make(actor=Actor("pat@example.com", True))])
    assert data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    assert len(rows) == 2 and list(rows[0]) == list(LOG_COLUMNS)
    assert rows[0]["QuickBooks file (SHA-256)"] == SHA_QB and rows[0]["Historical QuickBooks file (SHA-256)"] == ""
    assert rows[0]["Sign-in verified"] == "no" and rows[1]["Sign-in verified"] == "yes"
    assert rows[0]["Proposed journal entry"] == "1234.56" and rows[0]["Record digest"] == make().record_sha256


def test_log_csv_neutralizes_formula_like_names():
    r = make(actor=Actor("=HYPERLINK(\"x\")", True))
    row = next(csv.DictReader(io.StringIO(log_to_csv([r]).decode("utf-8-sig"))))
    assert row["Ran by"].startswith("'=")


def test_the_digest_does_not_depend_on_the_time_zone_the_timestamp_comes_back_in():
    from datetime import timedelta
    r = make()
    central = r.ran_at.astimezone(timezone(timedelta(hours=-5)))
    assert verify_record(replace(r, ran_at=central))
