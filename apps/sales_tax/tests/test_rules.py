"""Sales Tax rules, one at a time, on tiny hand-made frames.

``test_golden.py`` freezes whole runs on the sample files; these pin the individual rules behind
them (positional reads, blank is not a conflict, the dollar control, drop order, the trial balance
cache) so a failure points at the rule that moved. Nothing here touches ``data/``: the cache tests
redirect every path into a scratch folder.
"""
from io import BytesIO

import pandas as pd
import pytest

from apps.sales_tax import ingestion
from apps.sales_tax.cleanup import (
    DEFAULT_EXCLUDED_IDS,
    build_gl_account,
    clean_key,
    clean_key_series,
    compute_input_fingerprint,
    detect_mapping_conflicts,
    detect_trial_balance_conflicts,
    get_controlled_options,
    pad_numeric_segment,
    process_transactions,
    validate_inputs,
    validate_new_vendor_classifications,
)
from apps.sales_tax.excel_output import group_output_sheets, unique_sheet_name
from apps.sales_tax.ingestion import IngestionError, parse_excluded_ids
from apps.sales_tax.vendor_reconciliation import (
    clean_name_key_series,
    detect_duplicate_id_conflicts,
    diff_vendor_lists,
    validate_mapping_completeness,
    validate_reconciliation_inputs,
)

HEADERS = ["Vendor", "B", "Txn ID", "D", "Account", "Cost Center", "Amount", "H", "I", "Code", "K"]


def _source(*rows):
    """Source frame (11 columns) from ``(vendor, txn_id, account, cost_center, amount, code)``."""
    return pd.DataFrame(
        [[v, "", t, "", a, c, amt, "", "", code, ""] for v, t, a, c, amt, code in rows], columns=HEADERS)


def _mapping(*rows):
    """Mapping frame from ``(vendor, taxability, grouping)``; columns 1 and 2 are unused filler."""
    return pd.DataFrame([[v, "", "", t, g] for v, t, g in rows],
                        columns=["Vendor Name", "Vendor ID", "Notes", "Taxability", "Grouping"])


def _trial_balance(*rows):
    return pd.DataFrame([["", name, gl] for gl, name in rows], columns=["Misc", "Account Name", "GL Account"])


TB = _trial_balance(("001-12300-600000-000", "Supplies"))   # what cost center 123 + account 6000 builds
MAP = _mapping(("ACME", "Taxable", "Supplies"))


# --- key normalization ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, key", [
    (101806, "101806"), (101806.0, "101806"), ("101806.0", "101806"), ('"101806"', "101806"),
    (" 101 806 ", "101806"), ("\xa0ab-1\xa0", "AB-1"), ("'x1'", "X1"), (None, ""), ("", ""),
])
def test_keys_are_normalized_the_same_way_by_series_and_scalar(raw, key):
    assert clean_key(raw) == key
    assert clean_key_series(pd.Series([raw])).iloc[0] == key


def test_id_normalization_strips_inner_spaces_but_name_normalization_does_not():
    assert clean_key("ACME CO") == clean_key("ACMECO")
    names = clean_name_key_series(pd.Series(["Acme  Co", "ACMECO", " acme co "]))
    assert names.tolist() == ["ACME CO", "ACMECO", "ACME CO"]


# --- GL account building -------------------------------------------------------------------------

@pytest.mark.parametrize("value, length, side, expected", [
    ("123", 5, "right", "12300"),       # the macro's legacy trailing-zero padding
    ("123", 5, "left", "00123"),
    (123.0, 5, "right", "12300"),
    ("12345", 5, "right", "12345"),
    ("123456", 5, "right", None),       # too long
    ("12.5", 5, "right", None),         # not a whole number
    ("12A", 5, "right", None),
    ("", 5, "right", None),
    (None, 5, "right", None),
])
def test_segment_padding(value, length, side, expected):
    assert pad_numeric_segment(value, length, side) == expected


def test_gl_account_has_three_fixed_segments_and_pads_the_chosen_side():
    assert build_gl_account("123", "6000") == "001-12300-600000-000"
    assert build_gl_account("123", "6000", pad_side="left") == "001-00123-006000-000"
    assert build_gl_account("123", "bad") is None


# --- blank is not a conflict ---------------------------------------------------------------------

def test_a_blank_duplicate_is_not_a_mapping_conflict_but_two_different_values_are():
    blank_dup = _mapping(("ACME", "Taxable", "Supplies"), ("ACME", "", ""))
    same_dup = _mapping(("ACME", "Taxable", "Supplies"), ("ACME", "Taxable", "Supplies"))
    clash = _mapping(("ACME", "Taxable", "Supplies"), ("acme", "Exempt", "Supplies"))
    assert detect_mapping_conflicts(blank_dup) == []
    assert detect_mapping_conflicts(same_dup) == []
    found = detect_mapping_conflicts(clash)
    assert len(found) == 1 and "conflicting entries" in found[0]


def test_trial_balance_conflicts_follow_the_same_rule():
    assert detect_trial_balance_conflicts(_trial_balance(("A-1", "Cash"), ("A-1", ""))) == []
    assert len(detect_trial_balance_conflicts(_trial_balance(("A-1", "Cash"), ("A-1", "Bank")))) == 1


def test_the_classified_mapping_row_wins_whichever_comes_first():
    for rows in ([("ACME", "", ""), ("ACME", "Taxable", "Supplies")],
                 [("ACME", "Taxable", "Supplies"), ("ACME", "", "")]):
        result, _flags, _stats, _removed = process_transactions(
            _source(("ACME", "T1", "6000", "123", 10.0, "1")), _mapping(*rows), TB, [])
        assert result.loc[0, "Taxability"] == "Taxable"


def test_a_vendor_listing_conflict_ignores_blank_classification_but_not_blank_names():
    listing = pd.DataFrame({"ID": ["1", "1"], "Name": ["Acme", "Acme"], "Tax": ["Taxable", ""], "Grp": ["S", ""]})
    assert detect_duplicate_id_conflicts(listing, "ID", "Name", "Tax", "Grp") == []
    renamed = pd.DataFrame({"ID": ["1", "1"], "Name": ["Acme", "Acme LLC"], "Tax": ["", ""], "Grp": ["", ""]})
    assert len(detect_duplicate_id_conflicts(renamed, "ID", "Name", "Tax", "Grp")) == 1


# --- positional reads ----------------------------------------------------------------------------

def test_source_columns_are_read_by_position_not_by_header():
    base = _source(("ACME", "T1", "6000", "123", 10.0, "1"))
    renamed = base.copy()
    renamed.columns = [f"col{i}" for i in range(11)]
    left = process_transactions(base, MAP, TB, [])
    right = process_transactions(renamed, MAP, TB, [])
    pd.testing.assert_frame_equal(left[0].reset_index(drop=True).iloc[:, 11:], right[0].reset_index(drop=True).iloc[:, 11:])
    assert left[2] == right[2]


def test_a_source_file_with_the_wrong_column_count_is_refused():
    with pytest.raises(ValueError, match="exactly 11"):
        process_transactions(_source().iloc[:, :10], MAP, TB, [])
    assert any("exactly 11" in e for e in validate_inputs(_source(("A", "T", "1", "1", 1.0, "1")).iloc[:, :10], MAP, TB))


# --- dropping rows, and the dollar control -------------------------------------------------------

def test_drop_reasons_and_the_dollar_control_reconcile_to_the_penny():
    source = _source(
        ("ACME", "T1", "6000", "123", 100.10, "1"),
        ("ACME", "T1", "6000", "123", 50.05, "1"),          # duplicate transaction ID
        ("ACME", "T2", "6000", "123", 20.20, "X9"),         # code begins with a letter
        ("101806", "T3", "6000", "123", 30.30, "1"),        # excluded vendor
        ("NEWCO", "T4", "6000", "123", 5.05, "2"),          # not in the mapping
    )
    result, flags, stats, removed = process_transactions(source, MAP, TB, DEFAULT_EXCLUDED_IDS)

    assert stats["letter_rows_deleted"] == 1
    assert stats["excluded_rows_deleted"] == 1
    assert stats["duplicate_rows_deleted"] == 1
    assert stats["original_amount_total"] == pytest.approx(205.70)
    assert stats["retained_amount_total"] + stats["removed_amount_total"] == pytest.approx(205.70)
    assert stats["control_difference"] == 0.0
    assert sorted(removed["Removal Reason"]) == [
        "Code begins with a letter", "Duplicate transaction ID", "Excluded vendor ID"]
    assert len(result) == 2 and flags.tolist() == [False, True]    # sorted by code: ACME then NEWCO


def test_a_dropped_row_cannot_claim_a_transaction_id():
    """A letter-prefixed row is removed first, so a later valid row with the same ID is kept."""
    source = _source(("ACME", "T1", "6000", "123", 10.0, "X1"), ("ACME", "T1", "6000", "123", 20.0, "1"))
    result, _f, stats, _removed = process_transactions(source, MAP, TB, [])
    assert stats["duplicate_rows_deleted"] == 0
    assert result["Amount"].tolist() == [20.0]


def test_a_duplicate_names_the_row_that_was_kept():
    source = _source(("ACME", "T1", "6000", "123", 10.0, "1"), ("ACME", "T1", "6000", "123", 10.0, "1"))
    _r, _f, _s, removed = process_transactions(source, MAP, TB, [])
    assert removed["Source Excel Row"].tolist() == [3]
    assert removed["Retained Row (Duplicate)"].tolist() == ["2"]


def test_a_text_amount_becomes_a_number_in_the_output():
    source = _source(("ACME", "T1", "6000", "123", "125.50", "1"))
    result, _f, stats, _r = process_transactions(source, MAP, TB, [])
    assert result.loc[0, "Amount"] == 125.5
    assert stats["retained_amount_total"] == 125.5


def test_blank_and_non_numeric_amounts_are_caught_by_validation():
    source = _source(("A", "T1", "6000", "1", None, "1"), ("A", "T2", "6000", "1", "n/a", "1"))
    errors = validate_inputs(source, MAP, TB)
    assert any("blank Amount" in e for e in errors)
    assert any("non-numeric Amount" in e for e in errors)


def test_every_validation_problem_is_reported_in_one_pass():
    source = _source(("A", "T1", "6000", "1", 1.0, "1"))
    source.columns = ["x", "x"] + HEADERS[2:]                    # duplicate header
    errors = validate_inputs(source, _mapping(("A", "T", "G")).iloc[:, :3], TB.iloc[:, :2])
    assert len(errors) == 3
    assert any("duplicate column header" in e for e in errors)
    assert any("Vendor Mapping" in e for e in errors) and any("Trial Balance" in e for e in errors)


def test_unmatched_vendor_and_gl_rows_are_counted_and_new_vendors_flagged():
    source = _source(("ACME", "T1", "6000", "123", 1.0, "1"), ("NEWCO", "T2", "9999", "123", 1.0, "1"))
    result, flags, stats, _removed = process_transactions(source, MAP, TB, [])
    assert result.loc[0, "GL Account"] == "001-12300-600000-000"
    assert result.loc[0, "Account Name"] == "Supplies" and result.loc[1, "Account Name"] == ""
    assert (stats["matched_vendor_rows"], stats["unmatched_vendor_rows"]) == (1, 1)
    assert (stats["matched_gl_rows"], stats["unmatched_gl_rows"]) == (1, 1)   # NEWCO's account 9999 is not in the TB
    assert stats["new_vendor_rows"] == 1 and flags.sum() == 1


# --- new-vendor classification -------------------------------------------------------------------

def test_new_vendor_classification_needs_both_fields_and_no_collisions():
    edited = pd.DataFrame({"Vendor": ["NEWCO", "NEWCO", "ACME"],
                           "Taxability": ["Taxable", None, "Taxable"], "Grouping": ["", "Supplies", "Supplies"]})
    errors = " | ".join(validate_new_vendor_classifications(edited, MAP))
    assert "Missing Taxability for: NEWCO" in errors
    assert "Missing Grouping for: NEWCO" in errors
    assert "Duplicate vendor(s)" in errors
    assert "Already present in the mapping file: ACME" in errors


def test_dropdown_options_come_only_from_values_already_in_the_mapping():
    mapping = _mapping(("A", "Taxable", "Supplies"), ("B", "Exempt", ""), ("C", " Taxable ", "Parts"))
    assert get_controlled_options(mapping, 3) == ["Exempt", "Taxable"]
    assert get_controlled_options(mapping, 9) == []


# --- stale results -------------------------------------------------------------------------------

def test_the_input_fingerprint_changes_when_any_input_changes():
    args = dict(source_bytes=b"s", mapping_bytes=b"m", cached_tb_fingerprint="tb",
                excluded_ids=["1", "2"], pad_side="right")
    base = compute_input_fingerprint(**args)
    assert base == compute_input_fingerprint(**{**args, "excluded_ids": ["2", "1"]})   # order is irrelevant
    for change in ({"source_bytes": b"S"}, {"mapping_bytes": b"M"}, {"cached_tb_fingerprint": "tb2"},
                   {"excluded_ids": ["1"]}, {"pad_side": "left"}):
        assert compute_input_fingerprint(**{**args, **change}) != base


# --- output sheet names --------------------------------------------------------------------------

def test_sheet_names_are_legal_unique_and_never_take_the_main_sheet_name():
    taken = set()
    names = []
    for requested in ["Taxable", "taxable", "A/B:C*?", "X" * 40, "All Transactions", "", "Taxable"]:
        name = unique_sheet_name(requested, taken, "All Transactions")
        taken.add(name)
        names.append(name)
    assert len(set(names)) == len(names)
    assert all(len(n) <= 31 for n in names)
    assert not any(ch in n for n in names for ch in "/:*?[]\\")
    assert "All Transactions" not in names


def test_output_sheets_group_by_normalized_taxability_with_new_vendors_last():
    result = pd.DataFrame({"Taxability": ["Taxable", "TAXABLE ", "Exempt", ""], "Amount": [1, 2, 3, 4]})
    flags = pd.Series([False, False, False, True])
    groups = group_output_sheets(result, flags)
    assert list(groups) == ["TAXABLE", "EXEMPT", "New Vendors"]
    assert len(groups["TAXABLE"]) == 2


# --- vendor reconciliation -----------------------------------------------------------------------

def test_vendor_lists_split_into_added_removed_renamed_and_unchanged():
    old = pd.DataFrame({"ID": ["1", "2", "3"], "Name": ["Acme", "Beta", "Gamma"],
                        "Tax": ["Taxable", "Exempt", "Taxable"], "Grp": ["S", "P", "S"]})
    new = pd.DataFrame({"ID": ["1.0", "3", "4"], "Name": ["ACME ", "Gamma Inc", "Delta"]})
    added, removed, renamed, unchanged = diff_vendor_lists(old, new, "ID", "Name", "ID", "Name", "Tax", "Grp")
    assert added["Vendor ID"].tolist() == ["4"]
    assert removed["Vendor ID"].tolist() == ["2"]
    assert renamed["Vendor ID"].tolist() == ["3"] and renamed["New Name"].tolist() == ["Gamma Inc"]
    assert unchanged["Vendor ID"].tolist() == ["1.0"]          # case and spacing alone are not a rename
    assert unchanged["Taxability"].tolist() == ["Taxable"]


def test_a_classified_duplicate_in_the_old_listing_wins_the_diff():
    old = pd.DataFrame({"ID": ["1", "1"], "Name": ["Acme", "Acme"], "Tax": ["Taxable", ""], "Grp": ["S", ""]})
    new = pd.DataFrame({"ID": ["1"], "Name": ["Acme"]})
    _a, _r, _n, unchanged = diff_vendor_lists(old, new, "ID", "Name", "ID", "Name", "Tax", "Grp")
    assert unchanged["Taxability"].tolist() == ["Taxable"]


def test_reconciliation_input_validation_and_mapping_completeness():
    listing = pd.DataFrame({"ID": ["1"], "Name": ["A"]})
    errors = validate_reconciliation_inputs(listing, listing, "ID", "ID", "ID", "Name", "T", "T")
    assert len(errors) == 2
    active = pd.DataFrame({"Vendor ID": ["1", "2"], "Taxability": ["Taxable", ""], "Grouping": ["S", None]})
    assert validate_mapping_completeness(active) == [
        "Missing Taxability for Vendor ID(s): 2", "Missing Grouping for Vendor ID(s): 2"]


# --- excluded vendor IDs -------------------------------------------------------------------------

def test_excluded_ids_are_normalized_deduplicated_and_ordered():
    ids, error = parse_excluded_ids("101806, 103173.0\n102825\n\n101806, abc-9")
    assert ids == ["101806", "103173", "102825", "ABC-9"] and error is None


def test_an_excluded_id_csv_drops_a_header_row_and_merges_with_the_text():
    ids, error = parse_excluded_ids("1", csv_file=b"Vendor ID\n2\n3\n1\n")
    assert ids == ["1", "2", "3"] and error is None


def test_a_bad_exclusion_csv_keeps_the_text_ids_and_explains():
    ids, error = parse_excluded_ids("7", csv_file=b"a,b\n1,2\n")
    assert ids == ["7"] and "exactly one column" in error
    ids, error = parse_excluded_ids("7", csv_file=b"")
    assert ids == ["7"] and "empty" in error


# --- upload limits -------------------------------------------------------------------------------

def test_empty_oversized_and_unreadable_uploads_are_refused_with_a_message():
    with pytest.raises(IngestionError, match="no file"):
        ingestion.read_excel_upload(None)
    with pytest.raises(IngestionError, match="empty"):
        ingestion.read_excel_upload(b"")
    with pytest.raises(IngestionError, match="maximum allowed size"):
        ingestion._read_file_bytes(b"x" * 11, max_bytes=10, label="Test")
    with pytest.raises(IngestionError, match="could not be read"):
        ingestion.read_excel_upload(b"this is not a workbook")


# --- the shared trial balance cache ---------------------------------------------------------------

def _workbook(*rows) -> bytes:
    buffer = BytesIO()
    pd.DataFrame(rows, columns=["Misc", "Account Name", "GL Account"]).to_excel(buffer, index=False)
    return buffer.getvalue()


@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setattr(ingestion, "TRIAL_BALANCE_CACHE_PATH", tmp_path / "tb.xlsx")
    monkeypatch.setattr(ingestion, "TRIAL_BALANCE_BACKUP_PATH", tmp_path / "tb.backup.xlsx")
    return tmp_path


def test_an_empty_cache_reports_that_nothing_is_cached(cache):
    df, status = ingestion.load_trial_balance_cache_snapshot()
    assert df is None and not status.exists and not status.valid


def test_saving_a_trial_balance_makes_it_the_cache_and_reports_incomplete_accounts(cache):
    status = ingestion.save_trial_balance_cache(_workbook(("", "Cash", "A-1"), ("", "", "B-2")))
    assert status.valid and status.row_count == 2 and status.incomplete_count == 1
    df, loaded = ingestion.load_trial_balance_cache_snapshot()
    assert len(df) == 2 and loaded.fingerprint == status.fingerprint


def test_a_bad_upload_never_replaces_a_good_cache(cache):
    good = ingestion.save_trial_balance_cache(_workbook(("", "Cash", "A-1")))
    conflicting = _workbook(("", "Cash", "A-1"), ("", "Bank", "A-1"))
    with pytest.raises(IngestionError, match="conflicting account names"):
        ingestion.save_trial_balance_cache(conflicting)
    with pytest.raises(IngestionError):
        ingestion.save_trial_balance_cache(b"junk")
    assert ingestion.get_trial_balance_cache_status().fingerprint == good.fingerprint


def test_a_replaced_cache_keeps_one_backup_that_can_be_restored(cache):
    first = ingestion.save_trial_balance_cache(_workbook(("", "Cash", "A-1")))
    second = ingestion.save_trial_balance_cache(_workbook(("", "Cash", "A-1"), ("", "Bank", "B-2")))
    assert second.fingerprint != first.fingerprint
    restored = ingestion.restore_trial_balance_cache_backup()
    assert restored.fingerprint == first.fingerprint
    assert ingestion.get_trial_balance_cache_status().row_count == 1


def test_restoring_with_no_backup_is_refused_and_clearing_removes_both_files(cache):
    with pytest.raises(IngestionError, match="No Trial Balance cache backup"):
        ingestion.restore_trial_balance_cache_backup()
    ingestion.save_trial_balance_cache(_workbook(("", "Cash", "A-1")))
    ingestion.save_trial_balance_cache(_workbook(("", "Bank", "B-2")))
    assert ingestion.clear_trial_balance_cache() == 2
    assert not ingestion.get_trial_balance_cache_status().exists


# --- workflow state: what is blocking, and which input changed -----------------------------------

def test_status_names_every_missing_input_before_a_run():
    from apps.sales_tax.cleanup import run_status
    status = run_status(["Source Transactions", "Vendor Mapping"], False, False, 0.0, [], 0)
    assert status["step"] == 0
    assert status["blockers"] == ["Add Source Transactions.", "Add Vendor Mapping."]


def test_status_walks_ready_then_stale_then_failed_then_done():
    from apps.sales_tax.cleanup import run_status
    assert run_status([], False, False, 0.0, [], 0)["ready"] == "Ready to run."
    stale = run_status([], True, False, 0.0, ["Excluded vendor IDs", "GL account padding"], 0)
    assert stale["step"] == 1 and "Excluded vendor IDs, GL account padding" in stale["blockers"][0]
    failed = run_status([], True, True, 12.34, [], 0)
    assert failed["step"] == 2 and failed["tone"] == "fail" and "$12.34" in failed["blockers"][0]
    done = run_status([], True, True, 0.0, [], 3)
    assert done["step"] == 3 and done["blockers"] == [] and "3 transaction row(s)" in done["next_step"]


def test_a_control_difference_that_rounds_to_zero_is_not_a_blocker():
    from apps.sales_tax.cleanup import run_status
    assert run_status([], True, True, 0.004, [], 0)["blockers"] == []


def test_input_digests_name_each_input_and_change_with_it():
    from apps.sales_tax.cleanup import input_digests
    base = input_digests(b"s", b"m", "tb", ["2", "1"], "right")
    assert list(base) == ["Source Transactions file", "Vendor Mapping file", "Trial Balance cache",
                          "Excluded vendor IDs", "GL account padding"]
    assert input_digests(b"s", b"m", "tb", ["1", "2"], "right") == base
    changed = input_digests(b"s2", b"m", "tb", ["1", "2"], "left")
    assert [k for k in base if base[k] != changed[k]] == ["Source Transactions file", "GL account padding"]
    assert input_digests(None, None, None, [], "right")["Source Transactions file"] == ""
