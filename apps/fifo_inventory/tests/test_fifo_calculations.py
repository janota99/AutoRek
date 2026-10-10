"""The FIFO engine: strict oldest-first consumption, value conservation, and refusal of bad input.

These pin the rules in CLAUDE.md: usage is taken from the oldest layer first, layers never go
negative, a receipt's PRICE is its total extended value, and a preview (``calculate_fifo`` and
``stage_batch_calculation``) never changes the store's official layers.
"""
import json

import pandas as pd
import pytest

from apps.fifo_inventory.excel_export import _compute_summary_control_status
from apps.fifo_inventory.fifo_calculations import (
    _build_run_signature,
    calculate_fifo,
    stage_batch_calculation,
    summarize_control_counts,
)
from apps.fifo_inventory.user_inputs import ENGINE_VERSION

from .conftest import AS_OF, layer, receipts

EMPTY = pd.DataFrame()


def _run(beginning, rows, ending):
    frame = receipts(*rows) if rows else EMPTY
    return calculate_fifo(beginning, frame, ending, period=3, fiscal_year=2026, as_of_date=AS_OF)


def test_usage_comes_from_the_oldest_layer_first():
    beginning = [layer("2026-01-01", 100, 100.0), layer("2026-02-01", 100, 200.0)]
    on_hand, depleted, _receipts, m = _run(beginning, [], 150)
    assert m["usage_qty"] == 50
    assert [d["qty_consumed"] for d in depleted] == [50]
    assert depleted[0]["usage_value"] == pytest.approx(50.0)       # 50 units at the oldest $1.00
    assert [l["qty"] for l in on_hand] == [50, 100]                # newest layer untouched
    assert m["end_qty"] == 150
    assert m["end_val"] == pytest.approx(250.0)


def test_usage_spans_layers_and_marks_the_oldest_fully_depleted():
    beginning = [layer("2026-01-01", 100, 100.0), layer("2026-02-01", 100, 200.0)]
    on_hand, depleted, _r, m = _run(beginning, [], 50)
    assert [d["status"] for d in depleted] == ["Fully Depleted", "Partially Depleted"]
    assert m["usage_val"] == pytest.approx(100.0 + 100.0)          # all of layer 1, half of layer 2
    assert on_hand[0]["qty"] == 50 and on_hand[0]["total_value"] == pytest.approx(100.0)


def test_value_is_conserved_across_the_rollforward():
    beginning = [layer("2026-01-01", 1000, 12.34), layer("2026-02-01", 500, 7.77)]
    rows = [("2026-03-05", 400, 9.99), ("2026-03-20", 250, 8.88)]
    _oh, _dep, _r, m = _run(beginning, rows, 600)
    assert m["beg_val"] + m["purch_val"] - m["usage_val"] - m["end_val"] == pytest.approx(0.0, abs=1e-9)
    assert m["value_variance"] == pytest.approx(0.0, abs=1e-9)
    assert m["beg_qty"] + m["purch_qty"] - m["usage_qty"] == pytest.approx(m["end_qty"])


def test_receipt_price_is_the_total_extended_value():
    on_hand, _dep, raw, m = _run([], [("2026-03-05", 100, 250.0)], 100)
    assert raw[0]["unit_cost"] == pytest.approx(2.5)
    assert m["purch_val"] == pytest.approx(250.0)
    assert on_hand[0]["total_value"] == pytest.approx(250.0)


def test_ending_above_available_never_creates_a_negative_layer():
    """More ending stock than beginning + receipts: usage is zero, the gap is a quantity variance,
    and no layer goes negative. The control then FAILs rather than quietly absorbing it."""
    beginning = [layer("2026-01-01", 100, 100.0)]
    on_hand, depleted, _r, m = _run(beginning, [], 130)
    assert depleted == []
    assert all(l["qty"] > 0 and l["total_value"] >= 0 for l in on_hand)
    assert m["usage_val"] == 0
    assert m["variance_qty"] == pytest.approx(30)
    result = {"metrics": m, "on_hand": on_hand, "depleted": depleted, "receivings": [], "beg_variance_qty": 0}
    assert _compute_summary_control_status(result)["overall_status"] == "FAIL"


def test_receipts_are_consumed_in_date_order_not_file_order():
    rows = [("2026-03-20", 100, 300.0), ("2026-03-05", 100, 100.0)]    # newer listed first
    on_hand, depleted, _r, _m = _run([], rows, 100)
    assert depleted[0]["date"] == "2026-03-05"
    assert on_hand[0]["date"] == "2026-03-20"


def test_same_day_receipts_keep_file_order():
    rows = [("2026-03-05", 10, 10.0, "PO-1"), ("2026-03-05", 10, 20.0, "PO-2")]
    _oh, depleted, _r, _m = _run([], rows, 10)
    assert depleted[0]["po"] == "PO-1"


def test_adjacent_receipts_at_the_same_unit_cost_consolidate_with_lineage():
    rows = [("2026-03-01", 100, 100.0, "PO-1"), ("2026-03-02", 100, 100.0, "PO-2")]
    on_hand, _dep, _r, _m = _run([], rows, 200)
    assert len(on_hand) == 1
    assert on_hand[0]["receipt_count"] == 2
    assert on_hand[0]["po"] == "PO-1; PO-2"
    assert len(json.loads(on_hand[0]["source_receipts"])) == 2


def test_an_intervening_price_breaks_the_run_so_layers_do_not_merge_backward():
    rows = [("2026-03-01", 100, 100.0), ("2026-03-02", 100, 200.0), ("2026-03-03", 100, 100.0)]
    on_hand, _dep, _r, _m = _run([], rows, 300)
    assert len(on_hand) == 3


def test_opening_layers_are_never_consolidated_with_receipts():
    beginning = [layer("2026-01-01", 100, 100.0)]
    on_hand, _dep, _r, _m = _run(beginning, [("2026-03-01", 100, 100.0)], 200)
    assert len(on_hand) == 2


def test_layer_ids_name_the_fiscal_period():
    on_hand, _dep, _r, _m = _run([], [("2026-03-01", 100, 100.0)], 100)
    assert on_hand[0]["layer_id"] == "FY2026-P03-001"


def test_opening_layers_without_an_id_get_open_ids():
    on_hand, _dep, _r, _m = _run([layer("2026-01-01", 5, 5.0), layer("2026-02-01", 5, 5.0)], [], 10)
    assert [l["layer_id"] for l in on_hand] == ["OPEN-001", "OPEN-002"]


def test_partial_depletion_is_prorated_by_quantity():
    on_hand, depleted, _r, _m = _run([layer("2026-01-01", 300, 90.0)], [], 100)
    assert depleted[0]["usage_value"] == pytest.approx(60.0)       # 200 of 300 units at $0.30
    assert on_hand[0]["unit_cost"] == pytest.approx(0.30)


def test_age_is_measured_to_the_as_of_date():
    on_hand, _d, _r, _m = _run([layer("2026-08-01", 10, 10.0)], [], 10)
    assert on_hand[0]["age_days"] == 30


@pytest.mark.parametrize("rows, message", [
    ([("2026-03-01", 0, 10.0)], "must be positive"),
    ([("2026-03-01", -5, 10.0)], "must be positive"),
    ([("2026-03-01", 5, -10.0)], "cannot be negative"),
    ([("not a date", 5, 10.0)], "delivery date is invalid"),
    ([("2026-03-01", "abc", 10.0)], "not numeric"),
])
def test_bad_receipts_are_refused(rows, message):
    with pytest.raises(ValueError, match=message):
        _run([], rows, 0)


def test_receipts_without_a_date_column_are_refused():
    frame = receipts(("2026-03-01", 5, 10.0)).drop(columns=["DATE DELIVERED"])
    with pytest.raises(ValueError, match="missing DATE DELIVERED"):
        calculate_fifo([], frame, 5, period=3, fiscal_year=2026, as_of_date=AS_OF)


def test_negative_ending_quantity_is_refused():
    with pytest.raises(ValueError, match="cannot be negative"):
        _run([layer("2026-01-01", 10, 10.0)], [], -1)


def test_layers_out_of_date_order_are_refused():
    with pytest.raises(ValueError, match="chronological"):
        _run([layer("2026-03-01", 10, 10.0)], [("2026-01-01", 10, 10.0)], 20)


def test_negative_opening_layer_is_refused():
    with pytest.raises(ValueError, match="cannot be negative"):
        _run([{"date": "2026-01-01", "qty": -5, "unit_cost": 1.0, "total_value": -5.0}], [], 0)


def test_inputs_are_not_modified():
    beginning = [layer("2026-01-01", 100, 100.0)]
    frame = receipts(("2026-03-01", 50, 75.0))
    before_layers, before_frame = json.dumps(beginning), frame.copy()
    calculate_fifo(beginning, frame, 120, period=3, fiscal_year=2026, as_of_date=AS_OF)
    assert json.dumps(beginning) == before_layers
    pd.testing.assert_frame_equal(frame, before_frame)


# --- run signature -------------------------------------------------------------------------------

def _grid():
    return pd.DataFrame({"PRODUCT ALIAS": [2], "02": [10.0], "03": [5.0]})


def test_run_signature_is_stable_and_sensitive_to_every_input():
    def sig(year=2026, period=3, grid=None, price=10.0):
        return _build_run_signature(year, period, AS_OF, grid if grid is not None else _grid(),
                                    receipts(("2026-03-01", 5, price)))

    base = sig()
    assert base == sig()
    assert base != sig(period=4)
    assert base != sig(year=2025)
    assert base != sig(price=10.01)
    changed = _grid()
    changed.loc[0, "03"] = 6.0
    assert base != sig(grid=changed)
    assert ENGINE_VERSION  # the signature includes it; bumping the engine invalidates old previews


# --- staging a batch -----------------------------------------------------------------------------

def _staged(store, grid, rec):
    return stage_batch_calculation({2: "Product A"}, grid, rec, store, 2026, 3, AS_OF)


def test_staging_a_batch_computes_each_product_and_does_not_touch_the_store(make_store):
    store = make_store({2: "Product A"})
    store.set(2, [layer("2026-01-01", 100, 100.0)])
    official_before = store.all_layers()
    grid = pd.DataFrame({"PRODUCT ALIAS": [2], "02": [100.0], "03": [120.0]})
    rec = receipts(("2026-03-01", 50, 75.0, "", "", 2))

    results, staged, errors = _staged(store, grid, rec)

    assert errors == []
    assert results[0]["metrics"]["usage_qty"] == pytest.approx(30)
    assert sum(l["qty"] for l in staged[2]) == pytest.approx(120)
    assert store.all_layers() == official_before                   # preview never mutates


def test_a_missing_alias_is_reported_and_fails_its_control(make_store):
    store = make_store({2: "Product A"})
    grid = pd.DataFrame({"PRODUCT ALIAS": [99], "02": [1.0], "03": [1.0]})
    results, _staged_layers, errors = _staged(store, grid, EMPTY)
    assert "not found" in errors[0]["Error"]
    assert summarize_control_counts(results) == {"PASS": 0, "REVIEW": 0, "FAIL": 1}


def test_a_duplicated_alias_is_refused_not_guessed(make_store):
    store = make_store({2: "Product A"})
    grid = pd.DataFrame({"PRODUCT ALIAS": [2, 2], "02": [1.0, 1.0], "03": [1.0, 1.0]})
    results, _s, errors = _staged(store, grid, EMPTY)
    assert "more than once" in errors[0]["Error"]
    assert results[0]["alias_ambiguous"] is True


def test_period_one_reads_period_13_as_its_beginning(make_store):
    store = make_store({2: "Product A"})
    store.set(2, [layer("2026-01-01", 40, 40.0)])
    grid = pd.DataFrame({"PRODUCT ALIAS": [2], "13": [40.0], "01": [30.0]})
    results, _s, errors = stage_batch_calculation({2: "Product A"}, grid, EMPTY, store, 2027, 1, AS_OF)
    assert errors == []
    assert results[0]["metrics"]["usage_qty"] == pytest.approx(10)


def test_beginning_that_disagrees_with_the_master_grid_fails_the_control(make_store):
    store = make_store({2: "Product A"})
    store.set(2, [layer("2026-01-01", 90, 90.0)])                  # grid says 100 began the period
    grid = pd.DataFrame({"PRODUCT ALIAS": [2], "02": [100.0], "03": [90.0]})
    results, _s, _e = _staged(store, grid, EMPTY)
    assert results[0]["beg_variance_qty"] == pytest.approx(-10)
    assert summarize_control_counts(results)["FAIL"] == 1


def test_a_product_with_no_layers_and_a_beginning_quantity_gets_an_unpriced_carryover(make_store):
    """Known issue 5 in docs/known-issues.md: it surfaces only as REVIEW, never FAIL."""
    store = make_store({2: "Product A"})
    grid = pd.DataFrame({"PRODUCT ALIAS": [2], "02": [100.0], "03": [100.0]})
    results, _s, _e = _staged(store, grid, EMPTY)
    assert results[0]["on_hand"][0]["unit_cost"] == 0.0
    assert summarize_control_counts(results) == {"PASS": 0, "REVIEW": 1, "FAIL": 0}
