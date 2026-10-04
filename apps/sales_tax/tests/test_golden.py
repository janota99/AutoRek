"""Golden-file tests for Sales Tax: today's numbers, frozen, so a refactor cannot move one silently.

The inputs are the synthetic files in ``sample_data/`` (they exercise every drop reason: excluded
vendor, letter-prefixed code, duplicate transaction ID, new vendor). If a test here fails, the
output changed. Decide whether the change was intended (see CLAUDE.md: never change accounting
results silently); only then regenerate with ``GOLDEN_UPDATE=1`` and review the git diff.
"""
from pathlib import Path

import pandas as pd
import pytest

from apps.sales_tax.cleanup import (
    DEFAULT_EXCLUDED_IDS,
    process_transactions,
    validate_inputs,
    validate_new_vendor_classifications,
)
from apps.sales_tax.excel_output import build_updated_mapping_workbook, build_workbook
from apps.sales_tax.ingestion import parse_excluded_ids, read_excel_upload
from apps.sales_tax.vendor_reconciliation import build_mapping_workbook, diff_vendor_lists
from shared.golden import assert_matches_golden, workbook_snapshot

SAMPLES = Path(__file__).resolve().parents[3] / "sample_data"
GOLDEN = Path(__file__).resolve().parent / "golden"


def _load(name: str) -> pd.DataFrame:
    return read_excel_upload((SAMPLES / name).read_bytes())


@pytest.fixture(scope="module")
def inputs():
    return (_load("sales_tax_source_transactions.xlsx"),
            _load("sales_tax_vendor_mapping.xlsx"),
            _load("sales_tax_trial_balance.xlsx"))


@pytest.fixture(scope="module")
def cleaned(inputs):
    source, mapping, trial_balance = inputs
    return process_transactions(source, mapping, trial_balance, DEFAULT_EXCLUDED_IDS)


def test_sample_inputs_validate_clean(inputs):
    assert validate_inputs(*inputs) == []


def test_process_transactions(cleaned):
    result_df, new_vendor_flags, stats, removed_df = cleaned
    assert_matches_golden(GOLDEN, "process_transactions", {
        "result": result_df, "new_vendor_flags": new_vendor_flags,
        "stats": stats, "removed": removed_df,
    })


def test_dollar_control_check_is_zero(cleaned):
    """The download is disabled until this is $0.00; the sample must satisfy it."""
    assert cleaned[2]["control_difference"] == 0.0


def test_process_transactions_left_padding(inputs):
    source, mapping, trial_balance = inputs
    result_df, flags, stats, removed_df = process_transactions(
        source, mapping, trial_balance, DEFAULT_EXCLUDED_IDS, pad_side="left")
    assert_matches_golden(GOLDEN, "process_transactions_left_pad", {
        "result": result_df, "new_vendor_flags": flags, "stats": stats, "removed": removed_df,
    })


def test_cleaned_workbook(cleaned):
    result_df, flags, _stats, removed_df = cleaned
    buf, sheet_count = build_workbook(result_df, flags, removed_df)
    snapshot = workbook_snapshot(buf)
    snapshot["sheet_count"] = sheet_count
    assert_matches_golden(GOLDEN, "cleaned_workbook", snapshot)


def test_updated_mapping_workbook(inputs):
    _source, mapping, _tb = inputs
    classified = pd.DataFrame({"Vendor": ["Sample Pallet Brokers"],
                               "Taxability": ["Taxable"], "Grouping": ["Supplies"]})
    assert_matches_golden(GOLDEN, "updated_mapping_workbook",
                          workbook_snapshot(build_updated_mapping_workbook(mapping, classified)))


def test_new_vendor_classification_validation(inputs):
    _source, mapping, _tb = inputs
    blank = pd.DataFrame({"Vendor": ["Sample Pallet Brokers"], "Taxability": [""], "Grouping": [""]})
    assert_matches_golden(GOLDEN, "new_vendor_validation_blank",
                          validate_new_vendor_classifications(blank, mapping))


def test_validate_inputs_messages(inputs):
    source, mapping, trial_balance = inputs
    broken = source.iloc[:, :10].copy()
    assert_matches_golden(GOLDEN, "validate_inputs_wrong_column_count",
                          validate_inputs(broken, mapping.iloc[:, :3], trial_balance.iloc[:, :2]))
    bad_amount = source.copy()
    bad_amount[bad_amount.columns[6]] = bad_amount[bad_amount.columns[6]].astype(object)
    bad_amount.iloc[0, 6] = "n/a"
    bad_amount.iloc[1, 6] = None
    assert_matches_golden(GOLDEN, "validate_inputs_bad_amounts",
                          validate_inputs(bad_amount, mapping, trial_balance))


def test_vendor_diff_and_mapping_workbook():
    previous = _load("vendor_listing_previous.xlsx")
    updated = _load("vendor_listing_updated.xlsx")
    added, removed, renamed, unchanged = diff_vendor_lists(
        previous, updated, "Vendor ID", "Vendor Name", "Vendor ID", "Vendor Name",
        old_taxability_col="Taxability", old_grouping_col="Grouping")
    assert_matches_golden(GOLDEN, "vendor_diff", {
        "added": added, "removed": removed, "renamed": renamed, "unchanged": unchanged})
    for include_inactive in (False, True):
        buf = build_mapping_workbook(unchanged, renamed, added, removed, include_inactive=include_inactive)
        assert_matches_golden(GOLDEN, f"vendor_mapping_workbook_inactive_{include_inactive}",
                              workbook_snapshot(buf))


def test_parse_excluded_ids():
    ids, error = parse_excluded_ids("101806, 103173.0\n102825\n\n101806, ABC-9")
    assert_matches_golden(GOLDEN, "parse_excluded_ids", {"ids": ids, "error": error})
