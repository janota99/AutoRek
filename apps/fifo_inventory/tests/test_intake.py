"""Name-based material matching, fiscal-date receipt assignment, and how the beginning balance is derived."""
from datetime import date

import pandas as pd
import pytest

from apps.fifo_inventory import materials
from apps.fifo_inventory.fifo_calculations import _build_run_signature, stage_batch_calculation
from apps.fifo_inventory.intake import (
    PeriodWindow, input_exceptions, period_window, prepare_period_receipts, product_exception_rows,
    read_ending_report,
)
from apps.fifo_inventory.materials import AMBIGUOUS, OK, UNMAPPED, MaterialMatcher
from apps.fifo_inventory.user_inputs import INPUT_PROCESSING_VERSION, PRODUCTS
from shared.fiscal_calendar import FiscalCalendar, default_calendar

from .conftest import AS_OF, layer

PERIOD = 3
WINDOW = PeriodWindow(end=date(2026, 3, 28), start=date(2026, 3, 1), source="calendar")


@pytest.fixture
def matcher():
    return MaterialMatcher(PRODUCTS, {})


@pytest.fixture
def aliases_path(monkeypatch, tmp_path):
    path = tmp_path / "material_aliases.json"
    monkeypatch.setattr(materials, "ALIASES_PATH", path)
    return path


# --- materials: aliases and names ----------------------------------------------------------------

@pytest.mark.parametrize("spelling", ["LOW32 FILM", "Low 32 Film", "low32-film", " LOW 32/FILM ", "18", 18, 18.0])
def test_one_material_many_spellings(matcher, spelling):
    result = matcher.match(spelling)
    assert (result.status, result.alias) == (OK, 18)


def test_a_name_with_or_without_its_w_code_is_the_same_material(matcher):
    assert matcher.match("W0082 - BLUE CAPS").alias == 3
    assert matcher.match("Blue Caps").alias == 3
    assert matcher.match("NATURAL CAPS").alias == 4


def test_tracker_product_ids_map_to_the_materials_they_name(matcher):
    assert matcher.match("ITM-01").alias == 2      # W1228 preforms
    assert matcher.match("itm-02").alias == 3      # blue caps
    assert matcher.match("ITM-12").alias == 9      # Juniors labels: not alias 13
    assert matcher.match("ITM-08").alias == 10     # Plains labels
    assert matcher.match("ITM-29").alias == 30     # glue


def test_a_spelling_that_could_be_two_materials_is_ambiguous_never_guessed(matcher):
    result = matcher.match("W0082")                # the code of both the blue and the natural caps
    assert result.status == AMBIGUOUS and result.alias is None
    assert "BLUE CAPS" in result.detail and "NATURAL CAPS" in result.detail


def test_an_unknown_name_is_unmapped_and_a_suggestion_is_only_a_hint(matcher):
    result = matcher.match("PP 24 FLM")
    assert result.status == UNMAPPED and result.alias is None
    assert any("PP24 FILM" in hint for hint in matcher.suggestions("PP 24 FLM"))


def test_an_unknown_numeric_alias_does_not_match(matcher):
    assert matcher.match(999).status == UNMAPPED


def test_two_cells_naming_different_materials_conflict(matcher):
    result = matcher.match_first([5, "FOOD KING LABELS"])
    assert result.status == AMBIGUOUS and result.alias is None


def test_a_saved_spelling_wins_even_over_an_ambiguous_builtin(aliases_path):
    assert MaterialMatcher(PRODUCTS, {}).match("W0082").status == AMBIGUOUS
    materials.save_aliases({"W0082": 3})
    result = materials.current_matcher().match("w0082")
    assert (result.status, result.alias) == (OK, 3)


def test_saved_spellings_survive_a_missing_or_corrupt_file(aliases_path):
    assert materials.load_saved_aliases() == {}
    aliases_path.write_text("{not json", encoding="utf-8")
    assert materials.load_saved_aliases() == {}
    with pytest.raises(ValueError):
        materials.save_aliases({"X": 999})


# --- ending report -------------------------------------------------------------------------------

def _ending_rows(skip=(), **overrides):
    return {PRODUCTS[a]: overrides.get(PRODUCTS[a], 100.0) for a in PRODUCTS if PRODUCTS[a] not in skip}


def _ending_df(counts):
    return pd.DataFrame({"Item": list(counts), "Ending Quantity": list(counts.values())})


def test_a_one_row_per_material_report_is_matched_by_name(matcher):
    report = read_ending_report(_ending_df(_ending_rows()), matcher, PERIOD)
    assert report.errors == [] and report.matched == len(PRODUCTS)
    assert report.grid["03"].tolist() == [100.0] * len(PRODUCTS)


def test_a_wide_master_grid_reads_only_the_selected_periods_column(matcher):
    grid = pd.DataFrame({"PRODUCT ALIAS": list(PRODUCTS), "02": [999.0] * len(PRODUCTS), "03": [7.0] * len(PRODUCTS)})
    report = read_ending_report(grid, matcher, PERIOD)
    assert report.errors == []
    assert report.grid["03"].tolist() == [7.0] * len(PRODUCTS)
    assert list(report.grid.columns) == ["PRODUCT ALIAS", "DESCRIPTION", "03"]   # the prior-period column is not carried


def test_the_tracker_layout_with_a_period_column_keeps_only_that_period(matcher):
    rows = [{"Fiscal Period (1-13)": p, "Product ID / Item": name, "Ending Quantity": 10.0 * p}
            for p in (2, 3) for name in PRODUCTS.values()]
    report = read_ending_report(pd.DataFrame(rows), matcher, PERIOD)
    assert report.errors == [] and report.grid["03"].tolist() == [30.0] * len(PRODUCTS)


def test_a_material_counted_twice_is_an_error_and_is_not_summed(matcher):
    counts = pd.DataFrame({"Item": list(PRODUCTS.values()) + ["LOW32-FILM"], "Ending Quantity": [1.0] * len(PRODUCTS) + [5.0]})
    report = read_ending_report(counts, matcher, PERIOD)
    assert 18 in report.duplicates and len(report.duplicates[18]) == 2
    assert any("counted more than once" in e for e in report.errors)
    assert report.grid.loc[report.grid["PRODUCT ALIAS"] == 18, "03"].isna().all()


def test_a_material_absent_from_the_ending_report_carries_forward_without_an_exception(matcher):
    report = read_ending_report(_ending_df(_ending_rows(skip=("GLUE KRONES", "PP LABELS"))), matcher, PERIOD)
    assert report.errors == [] and report.warnings == []
    assert sorted(name for _a, name in report.missing) == ["GLUE KRONES", "PP LABELS"]
    assert input_exceptions(report, None) == []
    assert report.grid.loc[report.grid["PRODUCT ALIAS"] == 30, "03"].isna().all()   # no count, not a count of zero


def test_a_sparse_report_of_a_few_materials_is_complete_as_it_stands(matcher):
    report = read_ending_report(_ending_df({"PP LABELS": 5.0}), matcher, PERIOD)
    assert report.errors == [] and report.matched == 1


def test_an_unrecognized_line_with_a_quantity_is_reported_but_a_heading_is_ignored(matcher):
    counts = _ending_rows()
    frame = pd.concat([_ending_df(counts), pd.DataFrame({"Item": ["PALLETS", "LABELS"], "Ending Quantity": [40.0, None]})])
    report = read_ending_report(frame, matcher, PERIOD)
    assert report.errors == []
    assert [u["name"] for u in report.unmapped] == ["PALLETS"]


def test_a_blank_quantity_is_no_count_but_negative_and_text_are_invalid(matcher):
    counts = _ending_rows()
    frame = _ending_df(counts)
    frame["Ending Quantity"] = frame["Ending Quantity"].astype(object)
    frame.loc[0, "Ending Quantity"] = None
    frame.loc[1, "Ending Quantity"] = -4
    frame.loc[2, "Ending Quantity"] = "n/a"
    report = read_ending_report(frame, matcher, PERIOD)
    assert len(report.invalid) == 2                                  # negative and text; the blank is just no count
    assert report.grid["03"].isna().sum() == 3 and report.matched == len(PRODUCTS) - 3


def test_a_report_without_a_quantity_column_is_one_clear_error(matcher):
    report = read_ending_report(pd.DataFrame({"Item": ["PP LABELS"], "Notes": ["x"]}), matcher, PERIOD)
    assert report.structural and "no quantity column" in report.errors[0]


# --- receipts: dates against the fiscal calendar -------------------------------------------------

def _receipts(*rows):
    return pd.DataFrame([{"Item": item, "Delivery Date": day, "Quantity": qty, "Total Cost": cost, "PO #": f"PO-{i}"}
                         for i, (item, day, qty, cost) in enumerate(rows)])


@pytest.mark.parametrize("day, used", [
    ("2026-02-28", False),    # the day before the period starts
    ("2026-03-01", True),     # first day
    ("2026-03-28", True),     # last day
    ("2026-03-29", False),    # the day after
])
def test_receipts_are_assigned_by_delivery_date_at_the_period_boundaries(matcher, day, used):
    valid, errors, _w, debug = prepare_period_receipts(_receipts(("PP LABELS", day, 10, 5.0)), matcher, WINDOW, PERIOD)
    assert errors == []
    assert len(valid) == (1 if used else 0)
    assert debug["other_period_rows"] == (0 if used else 1)


def test_a_whole_year_log_uses_only_this_periods_rows_and_does_not_flag_the_others(matcher):
    log = _receipts(("PP LABELS", "2026-01-10", 1, 1.0), ("nope", "2026-01-11", 1, 1.0),
                    ("PP LABELS", "2026-03-10", 4, 2.0), ("PP LABELS", "2026-05-01", 1, 1.0))
    valid, errors, _w, debug = prepare_period_receipts(log, matcher, WINDOW, PERIOD)
    assert errors == [] and valid["QUANTITY"].tolist() == [4.0]      # the other periods' bad row is not this period's problem
    assert len(debug["notes"]) == 2


def test_the_period_comes_from_the_date_not_a_period_label(matcher):
    log = _receipts(("PP LABELS", "2026-03-10", 4, 2.0))
    log["Fiscal Period"] = "P09"
    valid, _e, warnings, _d = prepare_period_receipts(log, matcher, WINDOW, PERIOD)
    assert len(valid) == 1 and any("differs" in w for w in warnings)


def test_period_window_follows_the_calendar_including_period_one_and_thirteen():
    cal = default_calendar(2026, date(2026, 1, 1))
    first = period_window(2026, 1, cal, date(2026, 1, 28))
    assert (first.start, first.end, first.source) == (date(2026, 1, 1), date(2026, 1, 28), "calendar")
    second = period_window(2026, 2, cal, date(2026, 2, 25))
    assert second.start == date(2026, 1, 29)
    last = period_window(2026, 13, cal, date(2026, 12, 30))
    assert last.start == cal.period_ends[11] + pd.Timedelta(days=1) and last.end == cal.period_ends[12]
    assert last.year_end == cal.period_ends[12]


def test_without_a_calendar_for_that_year_the_last_close_sets_the_start():
    cal = default_calendar(2025, date(2025, 1, 1))
    window = period_window(2026, 3, cal, date(2026, 3, 28), last_close_end=date(2026, 2, 28))
    assert (window.start, window.source) == (date(2026, 3, 1), "last_close")
    nothing = period_window(2026, 3, cal, date(2026, 3, 28))
    assert nothing.start is None and nothing.source == "none"


def test_receipts_after_the_fiscal_year_end_are_flagged(matcher):
    cal = default_calendar(2026, date(2026, 1, 1))
    window = period_window(2026, 13, cal, cal.period_ends[12])
    log = _receipts(("PP LABELS", (cal.period_ends[12] + pd.Timedelta(days=5)).strftime("%Y-%m-%d"), 1, 1.0))
    _v, _e, warnings, _d = prepare_period_receipts(log, matcher, window, 13)
    assert any("after the fiscal year's last day" in w for w in warnings)


# --- receipts: costs and rejection reasons -------------------------------------------------------

def test_an_explicit_zero_cost_is_used_and_noted_but_a_blank_or_text_cost_is_rejected(matcher):
    log = _receipts(("PP LABELS", "2026-03-02", 10, 0.0), ("PP LABELS", "2026-03-03", 10, None),
                    ("PP LABELS", "2026-03-04", 10, "TBD"), ("PP LABELS", "2026-03-05", 10, "$1,250.50"))
    valid, errors, warnings, debug = prepare_period_receipts(log, matcher, WINDOW, PERIOD)
    assert valid["PRICE"].tolist() == [0.0, 1250.5]
    assert any("$0.00 (entered as zero, not blank)" in w for w in warnings)
    reasons = debug["rejected_rows"].set_index("SOURCE ROW")["REASON"]
    assert "Cost is missing" in reasons[3] and "Cost is not a number: 'TBD'" in reasons[4]
    assert errors and "2 receipt row(s)" in errors[0]


def test_rejected_rows_say_why_in_the_exceptions_table(matcher):
    log = _receipts(("PP LABELS", "2026-03-02", 0, 5.0), ("W0082", "2026-03-03", 5, 5.0), ("PP LABELS", "not a date", 5, 5.0))
    _v, _e, _w, debug = prepare_period_receipts(log, matcher, WINDOW, PERIOD)
    text = " | ".join(r["What"] for r in input_exceptions(None, debug))
    assert "Quantity must be positive" in text
    assert "ambiguous" in text and "BLUE CAPS" in text
    assert "Delivery date is missing" in text


def test_a_receiving_report_missing_a_required_column_is_named(matcher):
    valid, errors, _w, debug = prepare_period_receipts(pd.DataFrame({"Item": ["PP LABELS"], "Quantity": [1]}),
                                                       matcher, WINDOW, PERIOD)
    assert valid.empty and "Delivery Date" in errors[0] and debug["structural"]


def test_a_receiving_report_keeps_the_columns_the_engine_reads(matcher):
    valid, *_ = prepare_period_receipts(_receipts(("PP LABELS", "2026-03-02", 10, 5.0)), matcher, WINDOW, PERIOD)
    assert {"PRODUCT ALIAS", "DATE DELIVERED", "QUANTITY", "PRICE", "PO #", "ITEMS"} <= set(valid.columns)
    assert valid.loc[0, "PRODUCT ALIAS"] == 5


# --- through the engine --------------------------------------------------------------------------

def _stage(store, ending_counts, receipts_frame):
    report = read_ending_report(_ending_df(ending_counts), MaterialMatcher(PRODUCTS, {}), PERIOD)
    assert report.errors == []
    return stage_batch_calculation(PRODUCTS, report.grid, receipts_frame, store, 2026, PERIOD, AS_OF)


def test_same_day_receipts_are_consumed_in_the_order_the_report_lists_them(make_store):
    store = make_store(PRODUCTS)
    receipts_frame, *_ = prepare_period_receipts(
        _receipts(("PP LABELS", "2026-03-05", 100, 100.0), ("PP LABELS", "2026-03-05", 100, 200.0)),
        MaterialMatcher(PRODUCTS, {}), WINDOW, PERIOD)
    results, _staged, errors = _stage(store, _ending_rows(**{"PP LABELS": 150.0}), receipts_frame)
    assert errors == [] or all(e["Alias"] != 5 for e in errors)
    labels = next(r for r in results if r["alias"] == 5)
    assert labels["metrics"]["usage_qty"] == 50 and labels["metrics"]["usage_val"] == pytest.approx(50.0)
    assert [d["unit_cost"] for d in labels["depleted"]] == [1.0]                  # the first-listed receipt went first
    assert [round(l["unit_cost"], 6) for l in labels["on_hand"]] == [1.0, 2.0]    # different costs: not merged


def test_reversing_the_listing_order_reverses_which_cost_is_consumed(make_store):
    store = make_store(PRODUCTS)
    receipts_frame, *_ = prepare_period_receipts(
        _receipts(("PP LABELS", "2026-03-05", 100, 200.0), ("PP LABELS", "2026-03-05", 100, 100.0)),
        MaterialMatcher(PRODUCTS, {}), WINDOW, PERIOD)
    results, *_ = _stage(store, _ending_rows(**{"PP LABELS": 150.0}), receipts_frame)
    labels = next(r for r in results if r["alias"] == 5)
    assert labels["metrics"]["usage_val"] == pytest.approx(100.0)


def test_beginning_comes_from_the_saved_layers_with_no_beginning_column(make_store):
    store = make_store(PRODUCTS)
    store.set(5, [layer("2026-02-10", 1_000.0, 20.0)])
    results, staged, _errors = _stage(store, _ending_rows(**{"PP LABELS": 600.0}), pd.DataFrame())
    labels = next(r for r in results if r["alias"] == 5)
    assert labels["metrics"]["beg_qty"] == 1_000.0 and labels["beg_variance_qty"] == 0
    assert labels["metrics"]["usage_qty"] == 400.0 and labels["metrics"]["usage_val"] == pytest.approx(8.0)
    assert store.get(5) == [layer("2026-02-10", 1_000.0, 20.0)]        # calculating never changes saved layers


def test_a_material_with_no_layers_is_not_given_an_invented_zero_cost_layer(make_store):
    store = make_store(PRODUCTS)
    results, staged, _errors = _stage(store, _ending_rows(**{"PP LABELS": 500.0}), pd.DataFrame())
    labels = next(r for r in results if r["alias"] == 5)
    assert labels["metrics"]["beg_qty"] == 0
    rows = [r for r in product_exception_rows(results) if r["Item"].startswith("5 ")]
    assert rows and rows[0]["Severity"] == "ERROR"
    assert "no beginning layers on file" in rows[0]["What"] and "500" in rows[0]["What"]


def test_an_ending_count_above_beginning_plus_receipts_is_flagged_not_adjusted(make_store):
    store = make_store(PRODUCTS)
    store.set(5, [layer("2026-02-10", 100.0, 10.0)])
    receipts_frame, *_ = prepare_period_receipts(_receipts(("PP LABELS", "2026-03-05", 50, 5.0)),
                                                 MaterialMatcher(PRODUCTS, {}), WINDOW, PERIOD)
    results, *_ = _stage(store, _ending_rows(**{"PP LABELS": 400.0}), receipts_frame)
    labels = next(r for r in results if r["alias"] == 5)
    assert labels["metrics"]["end_qty"] == 400.0                       # operations' count is kept as reported
    assert any("250" in r["What"] and r["Severity"] == "ERROR" for r in product_exception_rows(results))


def test_the_run_signature_names_the_input_processing_version(monkeypatch):
    grid, rec = pd.DataFrame({"a": [1]}), pd.DataFrame({"b": [1]})
    before = _build_run_signature(2026, 3, AS_OF, grid, rec)
    from apps.fifo_inventory import fifo_calculations
    monkeypatch.setattr(fifo_calculations, "INPUT_PROCESSING_VERSION", INPUT_PROCESSING_VERSION + ".next")
    assert _build_run_signature(2026, 3, AS_OF, grid, rec) != before


# --- the continuous ledger: carry forward, explicit zero, receipts only --------------------------

def _grid_with(counts):
    """Ending grid from {alias: count}; every other material has no count."""
    report = read_ending_report(_ending_df({PRODUCTS[a]: q for a, q in counts.items()}),
                                MaterialMatcher(PRODUCTS, {}), PERIOD)
    assert report.errors == []
    return report.grid


def _stage_grid(store, grid, receipts_frame):
    return stage_batch_calculation(PRODUCTS, grid, receipts_frame, store, 2026, PERIOD, AS_OF)


def test_materials_with_no_count_and_no_receipts_keep_their_layers_unchanged(make_store):
    store = make_store(PRODUCTS)
    kept = [layer("2026-01-05", 1_000.0, 20.0), layer("2026-02-05", 500.0, 15.0)]
    store.set(6, kept)
    results, staged, errors = _stage_grid(store, _grid_with({}), pd.DataFrame())
    assert errors == []
    r = next(x for x in results if x["alias"] == 6)
    assert r["metrics"]["usage_qty"] == pytest.approx(0) and r["metrics"]["end_qty"] == 1_500.0
    assert r["metrics"]["end_val"] == pytest.approx(35.0) and r["depleted"] == [] and r["ending_carried_forward"]
    assert [(l["qty"], round(l["total_value"], 6)) for l in staged[6]] == [(1_000.0, 20.0), (500.0, 15.0)]
    assert product_exception_rows(results) == []


def test_receipts_are_appended_to_only_the_materials_named_and_nothing_is_used(make_store):
    store = make_store(PRODUCTS)
    store.set(2, [layer("2026-01-05", 1_000.0, 25.0)])
    store.set(3, [layer("2026-01-05", 400.0, 4.0)])
    frame, *_ = prepare_period_receipts(_receipts(("W1228 - PREFORMS / PLASTIPAK 8.5", "2026-03-05", 600, 18.0)),
                                        MaterialMatcher(PRODUCTS, {}), WINDOW, PERIOD)
    results, staged, errors = _stage_grid(store, _grid_with({}), frame)
    assert errors == []
    pre = next(x for x in results if x["alias"] == 2)
    assert pre["metrics"]["usage_qty"] == pytest.approx(0) and pre["metrics"]["end_qty"] == 1_600.0
    assert [l["unit_cost"] for l in staged[2]] == [pytest.approx(0.025), pytest.approx(0.03)]
    caps = next(x for x in results if x["alias"] == 3)
    assert caps["metrics"]["purch_qty"] == 0 and caps["metrics"]["end_qty"] == 400.0
    assert product_exception_rows(results) == []


def test_a_supplied_count_depletes_oldest_first_and_others_still_carry_forward(make_store):
    store = make_store(PRODUCTS)
    store.set(2, [layer("2026-01-05", 1_000.0, 10.0), layer("2026-02-05", 1_000.0, 20.0)])
    store.set(3, [layer("2026-01-05", 400.0, 4.0)])
    results, staged, _e = _stage_grid(store, _grid_with({2: 1_500.0}), pd.DataFrame())
    pre = next(x for x in results if x["alias"] == 2)
    assert pre["metrics"]["usage_qty"] == 500.0 and pre["metrics"]["usage_val"] == pytest.approx(5.0)
    assert not pre["ending_carried_forward"]
    assert next(x for x in results if x["alias"] == 3)["metrics"]["usage_qty"] == pytest.approx(0)


def test_an_explicit_zero_count_depletes_everything_but_an_omitted_count_does_not(make_store):
    store = make_store(PRODUCTS)
    store.set(2, [layer("2026-01-05", 1_000.0, 10.0)])
    store.set(3, [layer("2026-01-05", 400.0, 4.0)])
    results, staged, _e = _stage_grid(store, _grid_with({2: 0.0}), pd.DataFrame())
    zero = next(x for x in results if x["alias"] == 2)
    assert zero["metrics"]["usage_qty"] == 1_000.0 and zero["metrics"]["end_qty"] == 0
    assert staged[2] == [] and zero["metrics"]["usage_val"] == pytest.approx(10.0)
    omitted = next(x for x in results if x["alias"] == 3)
    assert omitted["metrics"]["end_qty"] == 400.0 and len(staged[3]) == 1


def test_a_count_plus_receipts_uses_beginning_plus_receipts_minus_count(make_store):
    store = make_store(PRODUCTS)
    store.set(2, [layer("2026-01-05", 1_000.0, 10.0)])
    frame, *_ = prepare_period_receipts(_receipts(("PREFORMS / PLASTIPAK 8.5", "2026-03-05", 500, 10.0)),
                                        MaterialMatcher(PRODUCTS, {}), WINDOW, PERIOD)
    results, *_ = _stage_grid(store, _grid_with({2: 1_200.0}), frame)
    pre = next(x for x in results if x["alias"] == 2)
    assert pre["metrics"]["usage_qty"] == 300.0 and pre["metrics"]["usage_val"] == pytest.approx(3.0)


def test_a_material_with_no_layers_no_count_and_no_receipts_is_left_alone(make_store):
    store = make_store(PRODUCTS)
    results, staged, errors = _stage_grid(store, _grid_with({}), pd.DataFrame())
    assert errors == [] and staged[18] == []
    assert all(r["metrics"]["end_qty"] == 0 for r in results) and product_exception_rows(results) == []
