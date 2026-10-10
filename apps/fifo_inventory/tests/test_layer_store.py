"""Period lifecycle and persistence: only ``commit_period`` changes official layers, periods close in
sequence, only the latest can reopen, and a snapshot restores exactly what was closed."""
import json
from datetime import date

import pytest

from apps.fifo_inventory import fifo_layer_store
from apps.fifo_inventory.fifo_layer_store import FIFOLayerStore, _canonicalize_layer, _layer_control_totals
from apps.fifo_inventory.user_inputs import PERIOD_12_OPENING_LAYERS, PRODUCTS, SNAPSHOT_SCHEMA_VERSION

from .conftest import layer

AS_OF = date(2026, 8, 31)
COUNTS = {"PASS": 2, "REVIEW": 0, "FAIL": 0}


def _close(store, year, period, layers_by_alias):
    store.commit_period(year, period, layers_by_alias, f"sig-{year}-{period}", COUNTS, AS_OF)


# --- layer normalization -------------------------------------------------------------------------

def test_unit_cost_is_derived_from_total_value():
    out = _canonicalize_layer({"date": "2026-01-01", "qty": 4, "unit_cost": 99.0, "total_value": 10.0})
    assert out["unit_cost"] == pytest.approx(2.5)               # the stated total wins over a stale rate


def test_total_value_comes_from_unit_cost_when_absent():
    out = _canonicalize_layer({"date": "2026-01-01", "qty": 4, "unit_cost": 2.5})
    assert out["total_value"] == pytest.approx(10.0)


def test_a_dust_quantity_is_zeroed_and_a_negative_one_is_refused():
    assert _canonicalize_layer({"qty": 0.00001, "total_value": 0.0})["qty"] == 0.0
    with pytest.raises(ValueError, match="cannot be negative"):
        _canonicalize_layer({"qty": -1, "total_value": 0.0})
    with pytest.raises(ValueError, match="cannot be negative"):
        _canonicalize_layer({"qty": 1, "total_value": -5.0})


@pytest.mark.parametrize("bad", [{"qty": float("nan")}, {"qty": float("inf")}, {"qty": "abc"}])
def test_non_finite_or_non_numeric_quantities_are_refused(bad):
    with pytest.raises(ValueError):
        _canonicalize_layer({**bad, "total_value": 1.0})


def test_a_layer_must_be_an_object():
    with pytest.raises(ValueError, match="JSON object"):
        _canonicalize_layer([1, 2, 3])


def test_control_totals_count_layers_quantity_and_value():
    totals = _layer_control_totals({2: [layer("2026-01-01", 10, 5.0)], 3: [layer("2026-01-01", 20, 7.5)]})
    assert totals == {"layer_count": 2, "quantity": 30.0, "value": 12.5}


# --- the store -----------------------------------------------------------------------------------

def test_reads_are_copies_so_callers_cannot_change_official_layers(make_store):
    store = make_store()
    store.set(2, [layer("2026-01-01", 10, 10.0)])
    store.get(2)[0]["qty"] = 999
    store.all_layers()[2][0]["qty"] = 999
    assert store.get(2)[0]["qty"] == 10


def test_seeding_reports_which_products_it_overwrote(make_store):
    store = make_store()
    store.set(2, [layer("2026-01-01", 1, 1.0)])
    overwritten = store.seed_opening_layers({2: [layer("2026-01-01", 5, 5.0)], 3: [layer("2026-01-01", 6, 6.0)]})
    assert overwritten == [2]
    assert store.current_qty(2) == 5 and store.current_qty(3) == 6


def test_sync_beginning_makes_an_unpriced_carryover_only_when_there_are_no_layers(make_store):
    store = make_store()
    assert store.sync_beginning(2, 50)[0] == {
        "date": "Prior Period Carryover", "qty": 50, "unit_cost": 0.0, "total_value": 0.0}
    assert store.sync_beginning(2, 0) == []
    store.set(2, [layer("2026-01-01", 10, 10.0)])
    assert store.sync_beginning(2, 50)[0]["date"] == "2026-01-01"


def test_variance_for_compares_stored_layers_to_the_expected_beginning(make_store):
    store = make_store()
    store.set(2, [layer("2026-01-01", 90, 180.0)])               # $2.00 average
    qty, value = store.variance_for(2, 100)
    assert qty == pytest.approx(-10) and value == pytest.approx(-20.0)


# --- closing a period ----------------------------------------------------------------------------

def test_period_keys_and_the_year_rollover():
    assert FIFOLayerStore.period_key(2026, 3) == "FY2026-P03"
    assert FIFOLayerStore.next_period(2026, 12) == (2026, 13)
    assert FIFOLayerStore.next_period(2026, 13) == (2027, 1)


def test_commit_replaces_layers_records_history_and_writes_a_verified_snapshot(make_store, snapshot_dir):
    store = make_store()
    store.set(2, [layer("2026-01-01", 100, 100.0)])
    staged = {2: [layer("2026-01-01", 60, 60.0)], 3: []}

    _close(store, 2026, 3, staged)

    assert store.get(2)[0]["qty"] == 60
    history = store.period_history()
    assert [h["period_key"] for h in history] == ["FY2026-P03"]
    assert history[0]["beginning_layers"][2][0]["qty"] == 100   # what the period started with
    assert history[0]["ending_layers"][2][0]["qty"] == 60
    assert (snapshot_dir / "FY2026-P03.json").is_file()
    assert not list(snapshot_dir.glob("*.tmp"))


def test_a_closed_period_cannot_be_closed_again(make_store):
    store = make_store()
    _close(store, 2026, 3, {2: [], 3: []})
    with pytest.raises(ValueError, match="already been closed"):
        _close(store, 2026, 3, {2: [], 3: []})


def test_periods_must_close_in_sequence(make_store):
    store = make_store()
    _close(store, 2026, 3, {2: [], 3: []})
    with pytest.raises(ValueError, match="next allowable period is FY2026-P04"):
        _close(store, 2026, 5, {2: [], 3: []})
    assert store.period_history()[-1]["period_key"] == "FY2026-P03"   # the refusal changed nothing


def test_period_13_rolls_into_period_1_of_the_next_year(make_store):
    store = make_store()
    _close(store, 2026, 13, {2: [], 3: []})
    assert store.sequence_error(2027, 1) is None
    assert "FY2027-P01" in store.sequence_error(2026, 13)


def test_a_failed_commit_leaves_official_layers_alone(make_store):
    store = make_store()
    store.set(2, [layer("2026-01-01", 100, 100.0)])
    _close(store, 2026, 3, {2: [layer("2026-01-01", 60, 60.0)], 3: []})
    before = store.all_layers()
    with pytest.raises(ValueError):
        _close(store, 2026, 9, {2: [], 3: []})
    assert store.all_layers() == before


# --- reopening -----------------------------------------------------------------------------------

def test_reopening_the_latest_period_restores_its_beginning_layers(make_store):
    store = make_store()
    store.set(2, [layer("2026-01-01", 100, 100.0)])
    _close(store, 2026, 3, {2: [layer("2026-01-01", 60, 60.0)], 3: []})

    assert store.can_reopen_latest(2026, 3)
    store.reopen_latest(2026, 3)

    assert store.get(2)[0]["qty"] == 100
    assert store.period_history() == []
    assert not store.period_is_closed(2026, 3)
    assert store.sequence_error(2026, 3) is None


def test_only_the_latest_closed_period_can_reopen(make_store):
    store = make_store()
    _close(store, 2026, 3, {2: [], 3: []})
    _close(store, 2026, 4, {2: [], 3: []})
    assert not store.can_reopen_latest(2026, 3)
    with pytest.raises(ValueError, match="Only the latest"):
        store.reopen_latest(2026, 3)
    store.reopen_latest(2026, 4)
    assert store.sequence_error(2026, 4) is None
    assert store.period_is_closed(2026, 3)


# --- snapshots -----------------------------------------------------------------------------------

def test_a_snapshot_round_trips_through_json(make_store):
    store = make_store()
    store.set(2, [layer("2026-01-01", 100, 100.0)])
    _close(store, 2026, 3, {2: [layer("2026-01-01", 60, 60.0)], 3: []})
    payload = store.to_json()

    other = make_store()
    other.from_json(payload)

    assert other.all_layers() == store.all_layers()
    assert [h["period_key"] for h in other.period_history()] == ["FY2026-P03"]
    assert json.loads(payload)["schema_version"] == SNAPSHOT_SCHEMA_VERSION


def test_a_fresh_session_recovers_the_latest_snapshot_from_disk(make_store, session_state):
    store = make_store()
    store.set(2, [layer("2026-01-01", 100, 100.0)])
    _close(store, 2026, 3, {2: [layer("2026-01-01", 60, 60.0)], 3: []})

    session_state.clear()                                         # the session is lost
    fresh = make_store()
    assert fresh.load_latest_snapshot_from_disk() == "FY2026-P03"
    assert fresh.get(2)[0]["qty"] == 60
    assert fresh.sequence_error(2026, 3) is not None              # P03 is closed again


def test_loading_with_no_snapshot_returns_none(make_store):
    assert make_store().load_latest_snapshot_from_disk() is None


def _payload(store, **changes):
    data = json.loads(store.to_json())
    data.update(changes)
    return json.dumps(data)


def test_a_snapshot_whose_totals_disagree_with_its_layers_is_refused(make_store):
    store = make_store()
    store.set(2, [layer("2026-01-01", 100, 100.0)])
    tampered = json.loads(store.to_json())
    tampered["inventory_layers"]["2"][0]["qty"] = 101
    with pytest.raises(ValueError, match="control totals"):
        make_store().from_json(json.dumps(tampered))


def test_a_newer_schema_is_refused_rather_than_misread(make_store):
    store = make_store()
    with pytest.raises(ValueError, match="newer than this app supports"):
        make_store().from_json(_payload(store, schema_version=SNAPSHOT_SCHEMA_VERSION + 1))


def test_unknown_aliases_and_negative_layers_are_refused(make_store):
    store = make_store()
    with pytest.raises(ValueError, match="unknown product aliases"):
        make_store().from_json(_payload(store, inventory_layers={"99": []}, control_totals=None))
    with pytest.raises(ValueError, match="cannot be negative"):
        make_store().from_json(_payload(
            store, inventory_layers={"2": [{"qty": -1, "total_value": 0}]}, control_totals=None))


def test_a_refused_snapshot_leaves_the_current_state_in_place(make_store):
    store = make_store()
    store.set(2, [layer("2026-01-01", 100, 100.0)])
    with pytest.raises(ValueError):
        store.from_json(_payload(store, schema_version=SNAPSHOT_SCHEMA_VERSION + 1))
    assert store.get(2)[0]["qty"] == 100


def test_the_period_12_seed_loads_cleanly_and_matches_its_golden_totals(make_store):
    """The P12 seed values are real opening balances. Any edit to them must be deliberate."""
    from shared.golden import assert_matches_golden
    from pathlib import Path

    store = make_store(PRODUCTS)
    store.seed_opening_layers(PERIOD_12_OPENING_LAYERS)
    totals = {}
    for alias in sorted(PERIOD_12_OPENING_LAYERS):
        layers = store.get(alias)
        totals[str(alias)] = {"layers": len(layers), "qty": sum(l["qty"] for l in layers),
                              "value": round(sum(l["total_value"] for l in layers), 2)}
    assert_matches_golden(Path(__file__).resolve().parent / "golden", "period_12_seed_totals", totals)
    assert fifo_layer_store._layer_control_totals(store.all_layers())["layer_count"] == sum(
        len(v) for v in PERIOD_12_OPENING_LAYERS.values())
