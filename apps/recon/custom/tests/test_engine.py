import pandas as pd

from apps.recon.custom.engine import reconcile, normalize_value
from apps.recon.custom.spec import ColumnPair, ReconSpec, find_column, missing_columns, validate_spec


def make_spec(**overrides):
    base = dict(
        name="t", label_a="QB", label_b="INF",
        amount=ColumnPair("Amt", "Total"),
        align=[ColumnPair("PO", "Ref", "digits")],
    )
    base.update(overrides)
    return ReconSpec(**base)


def frames(rows_a, rows_b):
    return (pd.DataFrame(rows_a, columns=["PO", "Amt"]), pd.DataFrame(rows_b, columns=["Ref", "Total"]))


def statuses(result):
    return list(result.dataset_a["Recon Status"]), list(result.dataset_b["Recon Status"])


def test_one_to_one_uses_exact_cents_and_normalized_keys():
    a, b = frames([("800001", "0.30"), ("800002", "10.00")], [("PO 800001", 0.1 + 0.2), ("PO 800002", "10.01")])
    result = reconcile(a, b, make_spec())
    assert statuses(result) == (["Matched", "Unmatched"], ["Matched", "Unmatched"])
    assert result.matches["Match Type"].tolist() == ["1 to 1"]
    assert result.overall == "PASS"


def test_no_closest_match_when_amounts_differ_by_a_cent():
    a, b = frames([("1", "100.00")], [("1", "100.01")])
    result = reconcile(a, b, make_spec())
    assert statuses(result) == (["Unmatched"], ["Unmatched"])


def test_alignment_group_must_agree():
    a, b = frames([("1", "5.00")], [("2", "5.00")])
    result = reconcile(a, b, make_spec())
    assert statuses(result)[0] == ["Unmatched"]
    assert "same alignment" in result.exceptions.iloc[0]["Reason"]


def test_duplicate_equal_rows_stay_ambiguous():
    a, b = frames([("1", "5.00"), ("1", "5.00")], [("1", "5.00"), ("1", "5.00")])
    result = reconcile(a, b, make_spec())
    assert statuses(result) == (["Ambiguous"] * 2, ["Ambiguous"] * 2)
    assert result.matches.empty
    assert result.controls.set_index("Check").loc["No ambiguous rows left for review", "Status"] == "REVIEW"


def test_many_to_one_needs_the_scale_setting():
    a, b = frames([("1", "40.00"), ("1", "60.00")], [("1", "100.00")])
    assert statuses(reconcile(a, b, make_spec(max_a_per_b=1)))[0] == ["Unmatched", "Unmatched"]
    result = reconcile(a, b, make_spec(max_a_per_b=2))
    assert statuses(result) == (["Matched", "Matched"], ["Matched"])
    assert result.matches.iloc[0]["Match Type"] == "2 QB to 1 INF"
    assert result.matches.iloc[0]["Difference"] == 0


def test_reverse_direction_is_its_own_setting():
    a, b = frames([("1", "100.00")], [("1", "30.00"), ("1", "20.00"), ("1", "50.00")])
    assert statuses(reconcile(a, b, make_spec(max_a_per_b=3)))[0] == ["Unmatched"]
    result = reconcile(a, b, make_spec(max_b_per_a=3))
    assert statuses(result) == (["Matched"], ["Matched"] * 3)
    assert result.matches.iloc[0]["Match Type"] == "3 INF to 1 QB"


def test_two_combinations_for_one_target_are_ambiguous():
    a, b = frames([("1", "40.00"), ("1", "60.00"), ("1", "30.00"), ("1", "70.00")], [("1", "100.00")])
    result = reconcile(a, b, make_spec(max_a_per_b=2))
    assert statuses(result) == (["Ambiguous"] * 4, ["Ambiguous"])
    assert result.matches.empty


def test_shared_row_between_targets_is_ambiguous():
    a, b = frames([("1", "40.00"), ("1", "60.00")], [("1", "100.00"), ("1", "100.00")])
    result = reconcile(a, b, make_spec(max_a_per_b=2))
    assert statuses(result) == (["Ambiguous"] * 2, ["Ambiguous"] * 2)


def test_one_to_one_beats_a_group_and_removes_rows_from_later_passes():
    a, b = frames([("1", "100.00"), ("1", "40.00"), ("1", "60.00")], [("1", "100.00")])
    result = reconcile(a, b, make_spec(max_a_per_b=2))
    assert statuses(result) == (["Matched", "Unmatched", "Unmatched"], ["Matched"])


def test_zero_rows_only_match_one_to_one():
    a, b = frames([("1", "0.00"), ("1", "5.00")], [("1", "5.00")])
    result = reconcile(a, b, make_spec(max_a_per_b=2))
    assert statuses(result) == (["Unmatched", "Matched"], ["Matched"])


def test_negative_amounts_net_to_the_cent():
    a, b = frames([("1", "(25.00)"), ("1", "75.00")], [("1", "50.00")])
    result = reconcile(a, b, make_spec(max_a_per_b=2))
    assert statuses(result)[0] == ["Matched", "Matched"]


def test_blank_amount_or_alignment_is_excluded_not_dropped():
    a, b = frames([("1", "abc"), (None, "5.00"), ("2", "5.00")], [("2", "5.00")])
    result = reconcile(a, b, make_spec())
    assert statuses(result)[0] == ["Excluded", "Excluded", "Matched"]
    assert result.controls.iloc[0]["Status"] == "PASS"
    assert len(result.exceptions) == 2


def test_no_alignment_columns_means_one_group():
    a, b = frames([("1", "5.00")], [("zzz", "5.00")])
    result = reconcile(a, b, make_spec(align=[]))
    assert statuses(result)[0] == ["Matched"]


def test_match_id_column_position_and_unique_id_labels():
    a = pd.DataFrame({"Inv": ["A-1", "A-2"], "PO": ["1", "2"], "Amt": ["5.00", "6.00"]})
    b = pd.DataFrame({"Ref": ["1"], "Total": ["5.00"]})
    spec = make_spec(id_a="Inv", match_id_position="after", match_id_after_a="Inv", match_id_after_b="Ref",
                     match_id_header="Link")
    result = reconcile(a, b, spec)
    assert list(result.dataset_a.columns) == ["Inv", "Link", "PO", "Amt", "Recon Status"]
    assert list(result.dataset_a["Link"]) == ["M-0001", ""]
    assert list(result.dataset_b.columns) == ["Ref", "Link", "Total", "Recon Status"]
    assert result.exceptions.iloc[0]["Row ID"] == "A-2"
    first = reconcile(a, b, make_spec(match_id_position="first"))
    assert list(first.dataset_a.columns)[0] == "Match ID"
    none = reconcile(a, b, make_spec(add_match_id=False))
    assert "Match ID" not in none.dataset_a.columns


def test_duplicate_id_column_is_flagged_for_review():
    a = pd.DataFrame({"Inv": ["X", "X"], "PO": ["1", "2"], "Amt": ["5.00", "6.00"]})
    b = pd.DataFrame({"Ref": ["1"], "Total": ["5.00"]})
    result = reconcile(a, b, make_spec(id_a="Inv"))
    row = result.controls.set_index("Check").loc["QB ID column 'Inv' is unique"]
    assert row["Status"] == "REVIEW"


def test_every_match_balances_and_bridge_ties():
    a, b = frames([("1", "40.00"), ("1", "60.00"), ("2", "9.00")], [("1", "100.00"), ("3", "1.00")])
    result = reconcile(a, b, make_spec(max_a_per_b=2))
    assert set(result.controls["Status"]) <= {"PASS", "REVIEW"}
    bridge = result.bridge.set_index("Dataset")
    assert bridge.loc["QB", "Matched Amount"] == 100.0 and bridge.loc["INF", "Matched Amount"] == 100.0
    assert bridge.loc["QB", "Total Amount"] == 109.0


def test_normalizers():
    assert normalize_value("PO 800-000", "digits") == "800000"
    assert normalize_value(" Foo  Bar ", "text") == "foo bar"
    assert normalize_value("A-1/b", "alnum") == "a1b"
    assert normalize_value("$1,000.10", "number") == "100010"
    assert normalize_value("10/02/2026", "date") == normalize_value(pd.Timestamp("2026-10-02"), "date")
    assert normalize_value(float("nan"), "text") is None
    assert normalize_value("--", "digits") is None


def test_spec_round_trip_and_clamping():
    spec = make_spec(max_a_per_b=3, id_b="Ref")
    again = ReconSpec.from_json(spec.to_json())
    assert again == spec
    clamped = ReconSpec.from_dict({"name": "x", "max_a_per_b": 99, "max_b_per_a": "bad", "align": [{"a": "x"}]})
    assert (clamped.max_a_per_b, clamped.max_b_per_a, clamped.align) == (6, 1, [])


def test_preset_fit_and_validation():
    spec = make_spec()
    assert find_column(["  po ", "Amt"], "PO") == "  po "
    assert missing_columns(spec, ["PO", "Amt"], ["Ref", "Total"]) == []
    assert missing_columns(spec, ["PO"], ["Ref", "Total"]) == ["QB has no column named 'Amt'."]
    assert validate_spec(spec, ["PO", "Amt"], ["Ref", "Total"]) == []
    assert validate_spec(make_spec(amount=None), ["PO"], ["Ref"])
    assert validate_spec(make_spec(label_b="qb"), ["PO", "Amt"], ["Ref", "Total"])


def test_search_limit_leaves_rows_unresolved(monkeypatch):
    from apps.recon.custom import engine
    monkeypatch.setattr(engine, "SEARCH_BUDGET", 1)
    a, b = frames([("1", "1.00"), ("1", "2.00"), ("1", "3.00"), ("1", "4.00")], [("1", "10.00")])
    result = reconcile(a, b, make_spec(max_a_per_b=3))
    assert statuses(result)[1] == ["Unmatched"]
    assert result.controls.set_index("Check").loc["Combination search completed", "Status"] == "REVIEW"
    assert result.warnings
