"""Every valid receipt is recognized exactly once: the cutoff, the register, late receipts, duplicates, Natural/CSI."""
import json
from datetime import date, datetime

import pandas as pd
import pytest

from apps.fifo_inventory.fifo_calculations import stage_batch_calculation
from apps.fifo_inventory.intake import (
    PeriodWindow, clamp_late_receipts, input_exceptions, opening_layer_evidence, prepare_period_receipts,
    receipt_register_counts, register_entries,
)
from apps.fifo_inventory.materials import MaterialMatcher
from apps.fifo_inventory.user_inputs import PRODUCTS
from apps.fifo_inventory.workbook_import import read_received_log

from .conftest import AS_OF, layer
from .test_workbook_import import COUNTS, LABEL, _log_row, _workbook

P12 = PeriodWindow(end=date(2026, 8, 29), start=date(2026, 8, 2), year_start=date(2025, 9, 28),
                   year_end=date(2026, 10, 3), source="calendar")
P13 = PeriodWindow(end=date(2026, 10, 3), start=date(2026, 8, 30), year_start=date(2025, 9, 28),
                   year_end=date(2026, 10, 3), source="calendar")
CUTOFF = date(2026, 8, 1)                          # the opening layers describe the position on this day (end of P11)

OPENING = {
    2: [{"date": "PD11-26: 07/03/26-07/30/26", "qty": 10_076_832, "unit_cost": 0.02544, "total_value": 256_354.58}],
    4: [layer("2026-07-21", 1_900_000, 13_054.73), layer("2026-07-27", 9_000_000, 64_170.0)],
}


def _heading(text):
    return [text] + [None] * 11


def _table(rows):
    table, errors, _notes = read_received_log(_workbook(rows, COUNTS), "RECEIVED LOG")
    assert errors == []
    return table


def _prepare(rows, window=P12, period=12, known=None, cutoff=CUTOFF, cutoff_period=11, layers=None, decisions=None):
    return prepare_period_receipts(
        _table(rows), MaterialMatcher(PRODUCTS, {}), window, period, material_columns=["ITEMS"], fiscal_year=2026,
        known_receipts=known, cutoff=cutoff, cutoff_period=cutoff_period,
        cutoff_layers=OPENING if layers is None else layers, decisions=decisions)


IN_P12 = _log_row("Preforms", datetime(2026, 8, 4), 1_000, 50.0, invoice="Inv#1", po="1")
CSI_731 = _log_row("CLOSURES", datetime(2026, 7, 31), 9_000_000, 65_520.0, invoice="Inv#2", po="2", vendor="CSI")
LATE_AFTER_CUTOFF = _log_row("Preforms", datetime(2026, 8, 1), 500, 25.0, period=None, invoice="Inv#3", po="3")
OLD_PRESUMED_DONE = _log_row("Preforms", datetime(2026, 6, 10), 700, 35.0, period="P.10 FYE 2026", invoice="Inv#4", po="4")
FUTURE = _log_row("Preforms", datetime(2026, 9, 10), 600, 30.0, period="P.13 FYE 2026", invoice="Inv#5", po="5")


# --- the cutoff: opening layers are the position, the register tracks what follows --------------------------

def test_receipts_dated_on_or_before_the_cutoff_are_not_imported_merely_for_being_unregistered():
    valid, errors, _w, debug = _prepare([IN_P12, OLD_PRESUMED_DONE,
                                         _log_row("Preforms", datetime(2026, 8, 1), 5, 1.0, period="P.11 FYE 2026")])
    assert valid["QUANTITY"].tolist() == [1_000] and errors == []
    assert debug["presumed_count"] == 2 and debug["late_rows"].empty
    assert any("presumed already inside the opening FIFO layers" in n for n in debug["notes"])


def test_a_receipt_after_the_cutoff_and_before_the_period_that_was_never_recorded_is_picked_up():
    valid, _e, _w, debug = _prepare([IN_P12, LATE_AFTER_CUTOFF],
                                    window=PeriodWindow(end=date(2026, 8, 29), start=date(2026, 8, 3), source="calendar"),
                                    cutoff=date(2026, 7, 15))
    assert sorted(valid["QUANTITY"]) == [500, 1_000] and len(debug["late_rows"]) == 1


def test_the_cutoff_is_stored_with_the_first_register_period_and_never_moves(make_store):
    store = make_store(PRODUCTS)
    store.set(4, [layer("2026-07-27", 9_000_000, 64_170.0)])
    assert store.receipt_cutoff() is None and store.cutoff_layers()[4][0]["qty"] == 9_000_000     # nothing closed yet
    store.commit_period(2026, 12, store.all_layers(), "sig", {}, date(2026, 8, 29), recognized_receipts=[],
                        receipt_cutoff=CUTOFF)
    assert store.receipt_cutoff() == CUTOFF
    store.set(4, [layer("2026-09-01", 1.0, 1.0)])                                  # layers move on after P12
    store.commit_period(2026, 13, store.all_layers(), "sig2", {}, date(2026, 10, 3), recognized_receipts=[],
                        receipt_cutoff=date(2026, 9, 1))                          # a later attempt cannot move it
    assert store.receipt_cutoff() == CUTOFF
    assert store.cutoff_layers()[4][0]["qty"] == 9_000_000                         # the opening position is preserved


def test_the_cutoff_and_opening_position_survive_a_snapshot_and_a_reopen_of_the_first_period(make_store):
    store = make_store(PRODUCTS)
    store.set(4, [layer("2026-07-27", 9_000_000, 64_170.0)])
    store.commit_period(2026, 12, store.all_layers(), "sig", {}, date(2026, 8, 29), recognized_receipts=[],
                        receipt_cutoff=CUTOFF)
    restored = make_store(PRODUCTS)
    restored.from_json(store.to_json())
    assert restored.receipt_cutoff() == CUTOFF and restored.cutoff_layers()[4][0]["qty"] == 9_000_000
    restored.reopen_latest(2026, 12)
    assert restored.receipt_cutoff() is None                                        # released: P12 can be re-run


def test_a_snapshot_from_before_the_register_loads_with_no_cutoff_and_no_register(make_store):
    store = make_store(PRODUCTS)
    store.commit_period(2026, 12, store.all_layers(), "sig", {}, date(2026, 8, 29))
    assert store.receipt_register() == [] and store.receipt_cutoff() is None
    old = json.loads(store.to_json())
    old["schema_version"] = 2
    make_store(PRODUCTS).from_json(json.dumps(old))


# --- late receipts crossing the cutoff -----------------------------------------------------------------

def test_the_july_31_caps_receipt_labelled_p12_is_recognized_in_p12_when_the_opening_layers_do_not_hold_it():
    valid, errors, warnings, debug = _prepare([IN_P12, CSI_731])
    assert errors == [] and len(valid) == 2 and debug["review_rows"].empty
    late = debug["late_rows"].iloc[0]
    assert str(late["DELIVERED"]) == "2026-07-31" and late["REPORTED PERIOD"] == LABEL and late["VENDOR"] == "CSI"
    assert not any("dated outside" in w for w in warnings)


def test_the_three_dates_stay_distinct_delivered_reported_and_recognized():
    valid, *_ = _prepare([CSI_731])
    entry = register_entries(valid, "FY2026-P12")[0]
    assert entry["delivered"] == "2026-07-31"                 # when it arrived
    assert entry["reported_period"] == LABEL                  # what operations called it
    assert entry["recognized_in"] == "FY2026-P12"             # where the books recognized it
    assert entry["late"] is True


def test_a_crossing_receipt_the_opening_layers_already_hold_is_not_imported_again():
    held = {4: OPENING[4] + [layer("2026-07-31", 9_000_000, 65_520.0)]}
    valid, errors, _w, debug = _prepare([IN_P12, CSI_731], layers=held)
    assert errors == [] and valid["PRODUCT ALIAS"].tolist() == [2]
    assert any("already included in the opening layers" in n for n in debug["notes"])
    assert len(debug["opening_rows"]) == 1 and debug["review_rows"].empty


@pytest.mark.parametrize("layers, why", [
    ({4: [layer("2026-07-31", 5_000_000, 20_000.0)]}, "different cost and quantity"),                       # same day, other cost
    ({4: [{"date": "PD11-26: 07/20/26-07/31/26", "qty": 9_000_000, "unit_cost": 0.0072, "total_value": 64_800.0}]}, "spans"),
    ({4: [{"date": "Unknown - Legacy Layer", "qty": 1000, "unit_cost": 0.01, "total_value": 10.0}]}, "no readable date"),
])
def test_evidence_that_cannot_tell_puts_that_one_receipt_up_for_one_time_review(layers, why):
    valid, errors, _w, debug = _prepare([IN_P12, CSI_731], layers=layers)
    assert valid["PRODUCT ALIAS"].tolist() == [2]                       # held back, not guessed
    review = debug["review_rows"]
    assert len(review) == 1 and why in review.iloc[0]["WHY"] and str(review.iloc[0]["DELIVERED"]) == "2026-07-31"
    assert any("one-time review" in e or "Confirm each one once" in e for e in errors)
    assert any("needs a one-time review" in r["What"] for r in input_exceptions(None, debug))


def test_a_one_time_decision_to_include_recognizes_the_receipt():
    layers = {4: [layer("2026-07-31", 5_000_000, 20_000.0)]}
    _v, _e, _w, debug = _prepare([CSI_731], layers=layers)
    key = debug["review_rows"].iloc[0]["_KEY"]
    valid, errors, _w2, debug2 = _prepare([CSI_731], layers=layers, decisions={key: "include"})
    assert errors == [] and valid["PRODUCT ALIAS"].tolist() == [4] and debug2["review_rows"].empty
    assert debug2["decided_rows"].iloc[0]["DECISION"] == "include"


def test_a_one_time_decision_that_it_was_already_in_the_opening_layers_is_recorded_so_it_is_never_asked_again():
    layers = {4: [layer("2026-07-31", 5_000_000, 20_000.0)]}
    _v, _e, _w, debug = _prepare([CSI_731], layers=layers)
    key = debug["review_rows"].iloc[0]["_KEY"]
    valid, errors, _w2, debug2 = _prepare([CSI_731], layers=layers, decisions={key: "opening"})
    assert valid.empty and errors == []
    opening = debug2["opening_rows"]
    entries = register_entries(opening, "FY2026-P12", status="in opening layers")
    assert entries[0]["status"] == "in opening layers" and entries[0]["recognized_in"] == "opening layers"
    # next period: the register accounts for it, so there is nothing to ask and nothing to import
    again, errors2, _w3, debug3 = _prepare([CSI_731], window=P13, period=13, known=receipt_register_counts(entries),
                                           layers=layers, cutoff_period=12)
    assert again.empty and errors2 == [] and debug3["review_rows"].empty


def test_the_merged_period_heading_is_supporting_evidence_when_column_k_is_blank():
    rows = [_heading("PERIOD 12 FYE 8/2-29/2026"), _log_row("CLOSURES", datetime(2026, 7, 31), 9_000_000, 65_520.0,
                                                           period=None, invoice="Inv#h", vendor="CSI")]
    valid, errors, _w, debug = _prepare(rows)
    assert errors == [] and valid["PRODUCT ALIAS"].tolist() == [4]
    assert debug["late_rows"].iloc[0]["REPORTED PERIOD"].startswith("PERIOD 12 FYE")


def test_column_k_and_the_heading_disagreeing_is_a_conflict_for_review_not_a_pick():
    rows = [_heading("PERIOD 11 FYE 7/5-8/1/2026"), CSI_731]                  # K says P.12, the heading says period 11
    valid, errors, _w, debug = _prepare(rows)
    assert valid.empty and len(debug["review_rows"]) == 1
    assert "heading above it" in debug["review_rows"].iloc[0]["WHY"] and errors


def test_a_heading_in_force_covers_the_rows_below_it_until_the_next_heading():
    rows = [_heading("PERIOD 12 FYE 8/2-29/2026"), IN_P12, _heading("PERIOD 13 FYE 8/29/26-pm-10/3/2026"), FUTURE]
    table = _table(rows)
    sections = table.loc[table["ITEMS"].notna(), "SECTION PERIOD"].tolist()
    assert sections[0].startswith("PERIOD 12") and sections[1].startswith("PERIOD 13")


def test_opening_layer_evidence_directly():
    assert opening_layer_evidence(4, date(2026, 7, 31), 9e6, 65_520.0, OPENING)[0] == "not_included"
    held = {4: [layer("2026-07-31", 9_000_000, 65_520.0)]}
    assert opening_layer_evidence(4, date(2026, 7, 31), 9e6, 65_520.0, held)[0] == "included"
    assert opening_layer_evidence(2, date(2026, 7, 20), 1000, 50.0, OPENING)[0] == "unclear"      # inside a range layer
    assert opening_layer_evidence(9, date(2026, 7, 31), 1000, 50.0, OPENING)[0] == "not_included"  # no layers at all


# --- previously processed receipts, including fully depleted ones ----------------------------------

def test_a_receipt_in_the_register_is_not_imported_again():
    first, *_ = _prepare([IN_P12])
    register = receipt_register_counts(register_entries(first, "FY2026-P12"))
    again, errors, _w, debug = _prepare([IN_P12], known=register)
    assert again.empty and errors == []
    assert any("already in the receipt register" in n for n in debug["notes"])


def test_a_fully_depleted_receipt_is_still_known_because_the_register_is_not_the_layers(make_store):
    store = make_store(PRODUCTS)
    first, *_ = _prepare([IN_P12])
    ending = pd.DataFrame({"PRODUCT ALIAS": list(PRODUCTS), "12": [0.0] * len(PRODUCTS)})      # everything consumed
    _results, staged, _e = stage_batch_calculation(PRODUCTS, ending, first, store, 2026, 12, AS_OF)
    assert staged[2] == []                                                                       # no layer remains
    store.commit_period(2026, 12, staged, "sig", {"PASS": 29, "REVIEW": 0, "FAIL": 0}, date(2026, 8, 29),
                        recognized_receipts=register_entries(first, "FY2026-P12"), receipt_cutoff=CUTOFF)
    again, *_ = _prepare([IN_P12], window=P13, period=13, known=receipt_register_counts(store.receipt_register()))
    assert again.empty                                                  # consumed in full, yet never reprocessed


def test_the_register_keeps_the_receipt_as_reported_and_where_it_was_recognized():
    first, *_ = _prepare([IN_P12, CSI_731])
    entries = register_entries(first, "FY2026-P12")
    caps = next(e for e in entries if e["alias"] == 4)
    assert caps["item"] == "CLOSURES" and caps["vendor"] == "CSI" and caps["invoice"] == "Inv#2"
    assert caps["total"] == 65_520.0 and caps["qty"] == 9_000_000 and caps["status"] == "recognized"
    assert next(e for e in entries if e["alias"] == 2)["late"] is False


def test_the_register_survives_a_snapshot_round_trip_and_reopen_releases_it(make_store):
    store = make_store(PRODUCTS)
    first, *_ = _prepare([IN_P12])
    entries = register_entries(first, "FY2026-P12")
    store.commit_period(2026, 12, store.all_layers(), "sig", {}, date(2026, 8, 29),
                        recognized_receipts=entries, receipt_cutoff=CUTOFF)
    payload = store.to_json()
    assert json.loads(payload)["schema_version"] == 3
    restored = make_store(PRODUCTS)
    restored.from_json(payload)
    assert restored.receipt_register() == entries
    restored.reopen_latest(2026, 12)
    assert restored.receipt_register() == []


def test_a_late_receipt_is_recognized_once_then_never_again():
    first, *_ = _prepare([IN_P12, CSI_731])
    register = receipt_register_counts(register_entries(first, "FY2026-P12"))
    second, errors, *_ = _prepare([IN_P12, CSI_731], window=P13, period=13, known=register, cutoff_period=12)
    assert second.empty and errors == []


def test_future_dated_receipts_stay_pending_and_arrive_in_their_own_period():
    in_p12, _e, _w, debug = _prepare([IN_P12, FUTURE])
    assert in_p12["QUANTITY"].tolist() == [1_000] and any("later periods" in n for n in debug["notes"])
    register = receipt_register_counts(register_entries(in_p12, "FY2026-P12"))
    in_p13, *_ = _prepare([IN_P12, FUTURE], window=P13, period=13, known=register, cutoff_period=12)
    assert in_p13["QUANTITY"].tolist() == [600] and in_p13["RECEIPT STATUS"].tolist() == ["in period"]


def test_a_late_receipt_older_than_the_carried_layers_joins_the_queue_after_them(make_store):
    store = make_store(PRODUCTS)
    store.set(4, [layer("2026-07-27", 10_900_000, 77_224.73)])
    old = list(CSI_731)
    old[3] = datetime(2026, 7, 20)                                             # older than the 7/27 layer
    valid, *_ = _prepare([old], layers={})
    engine = clamp_late_receipts(valid, store.all_layers())
    assert str(engine.iloc[0]["DATE DELIVERED"].date()) == "2026-07-27"       # engine order only
    assert str(pd.Timestamp(engine.iloc[0]["ORIGINAL DATE DELIVERED"]).date()) == "2026-07-20"
    ending = pd.DataFrame({"PRODUCT ALIAS": list(PRODUCTS), "12": [float("nan")] * len(PRODUCTS)})
    results, _staged, errors = stage_batch_calculation(PRODUCTS, ending, engine, store, 2026, 12, AS_OF)
    assert errors == [] and next(r for r in results if r["alias"] == 4)["metrics"]["end_qty"] == 19_900_000
    assert register_entries(engine, "FY2026-P12")[0]["delivered"] == "2026-07-20"       # the register keeps the truth


# --- preventing duplicate inventory recognition ----------------------------------------------------

def test_identical_looking_deliveries_are_each_recognized_once_not_collapsed():
    twin_a = _log_row("Preforms", datetime(2026, 8, 15), 1_695_168, 42_023.21, invoice="Inv#10", po="3671")
    twin_b = _log_row("Preforms", datetime(2026, 8, 15), 1_695_168, 42_023.21, invoice="Inv#11", po="3657")
    valid, _e, warnings, _d = _prepare([twin_a, twin_b])
    assert len(valid) == 2 and not any("may be duplicates" in w for w in warnings)


def test_rows_identical_in_every_field_are_flagged_as_possible_duplicates_but_kept():
    same = _log_row("Preforms", datetime(2026, 8, 15), 100, 5.0, invoice="Inv#9", po="9")
    valid, _e, warnings, _d = _prepare([same, list(same)])
    assert len(valid) == 2 and any("may be duplicates" in w for w in warnings)


def test_with_one_of_two_identical_rows_registered_only_the_second_is_new():
    same = _log_row("Preforms", datetime(2026, 8, 15), 100, 5.0, invoice="Inv#9", po="9")
    first, *_ = _prepare([same])
    register = receipt_register_counts(register_entries(first, "FY2026-P12"))
    valid, _e, _w, debug = _prepare([same, list(same)], known=register)
    assert len(valid) == 1 and any("already in the receipt register" in n for n in debug["notes"])


def test_an_old_processed_row_cannot_hide_a_new_identical_one():
    old = _log_row("Preforms", datetime(2026, 8, 15), 100, 5.0, invoice="Inv#9", po="9")
    register = {(2, "2026-08-15", 100.0, 5.0, "9", "INV9"): 1}
    valid, *_ = _prepare([old, list(old)], known=register)
    assert len(valid) == 1


def test_recognizing_the_same_log_in_consecutive_periods_never_double_counts():
    rows = [IN_P12, CSI_731, LATE_AFTER_CUTOFF, FUTURE, OLD_PRESUMED_DONE]
    p12, *_ = _prepare(rows, cutoff=date(2026, 7, 15))
    p13, *_ = _prepare(rows, window=P13, period=13, known=receipt_register_counts(register_entries(p12, "FY2026-P12")),
                       cutoff=date(2026, 7, 15), cutoff_period=11)
    recognized = pd.concat([p12, p13])
    assert recognized["_SOURCE_ROW"].is_unique                                     # no row recognized twice
    assert sorted(recognized["QUANTITY"]) == sorted([1_000, 9_000_000, 500, 600])  # and none missed (old one is pre-cutoff)


# --- Natural Caps and CSI Caps are one material -------------------------------------------------------

@pytest.mark.parametrize("spelling", ["Natural Caps", "NATURAL CAPS", "Natural Caps-SSB", "W0082 - NATURAL CAPS",
                                      "CSI Caps", "CSI", "CLOSURES", "CAPS - CLOSURES"])
def test_every_natural_and_csi_spelling_is_alias_4(spelling):
    assert MaterialMatcher(PRODUCTS, {}).match(spelling).alias == 4


def test_natural_and_csi_receipts_form_one_continuous_ledger_with_the_originals_kept(make_store):
    store = make_store(PRODUCTS)
    store.set(4, [layer("2026-06-10", 1_000_000, 6_870.0), layer("2026-07-27", 9_000_000, 64_170.0)])   # natural-era layers
    rows = [_log_row("CSI Caps", datetime(2026, 8, 5), 9_000_000, 65_520.0, invoice="Inv#c1", vendor="CSI"),
            _log_row("Natural Caps", datetime(2026, 8, 6), 1_000_000, 7_000.0, invoice="Inv#n1", vendor="Berricap")]
    valid, errors, *_ = _prepare(rows)
    assert errors == [] and set(valid["PRODUCT ALIAS"]) == {4}
    entries = register_entries(valid, "FY2026-P12")
    assert {(e["item"], e["vendor"]) for e in entries} == {("CSI Caps", "CSI"), ("Natural Caps", "BERRICAP")}
    ending = pd.DataFrame({"PRODUCT ALIAS": list(PRODUCTS), "12": [float("nan")] * len(PRODUCTS)})
    ending.loc[ending["PRODUCT ALIAS"] == 4, "12"] = 18_000_000
    results, staged, errors = stage_batch_calculation(PRODUCTS, ending, valid, store, 2026, 12, AS_OF)
    assert errors == []
    metrics = next(r for r in results if r["alias"] == 4)["metrics"]
    assert metrics["beg_qty"] == 10_000_000 and metrics["purch_qty"] == 10_000_000 and metrics["usage_qty"] == 2_000_000
    depleted = next(r for r in results if r["alias"] == 4)["depleted"]
    assert depleted[0]["qty_consumed"] == 1_000_000 and depleted[0]["unit_cost"] == pytest.approx(0.00687)
    assert sum(l["qty"] for l in staged[4]) == 18_000_000                                                       # one inventory history


def test_the_ending_report_counts_natural_and_csi_together_and_says_so():
    from apps.fifo_inventory.workbook_import import read_operations_workbook
    from .test_workbook_import import GOOD_ROWS, WINDOW
    result = read_operations_workbook(_workbook(GOOD_ROWS, COUNTS), 2026, 12, MaterialMatcher(PRODUCTS, {}), WINDOW)
    assert result.ending.grid.set_index("PRODUCT ALIAS")["12"][4] == 26_700_000
    assert any("Natural Caps-SSB 0 + CSI Caps 26,700,000" in n for n in result.notes)
