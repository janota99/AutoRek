"""The workpaper: Checks first, a 13-period Period End Summary, and the Receipts register view."""
import io
from datetime import date, datetime

import pandas as pd
import pytest
from openpyxl import load_workbook

from apps.fifo_inventory.excel_export import export_master_excel
from apps.fifo_inventory.fifo_calculations import stage_batch_calculation, summarize_control_counts
from apps.fifo_inventory.intake import (
    clamp_late_receipts, input_exceptions, prepare_period_receipts, product_exception_rows,
)
from apps.fifo_inventory.materials import MaterialMatcher
from apps.fifo_inventory.user_inputs import PRODUCTS

from .conftest import layer
from .test_receipt_register import CSI_731, CUTOFF, IN_P12, P12, _table

PRODUCT_COUNT = len(PRODUCTS)


def _run(make_store, rows=(IN_P12, CSI_731), counts=None, history=None):
    store = make_store(PRODUCTS)
    store.set(2, [layer("2026-07-01", 10_000, 100.0)])
    store.set(4, [layer("2026-07-27", 1_000_000, 7_000.0)])
    receipts, _errors, _w, debug = prepare_period_receipts(
        _table(list(rows)), MaterialMatcher(PRODUCTS, {}), P12, 12, material_columns=["ITEMS"], fiscal_year=2026,
        cutoff=CUTOFF, cutoff_period=11, cutoff_layers={})
    engine = clamp_late_receipts(receipts, store.all_layers())
    counts = counts or {2: 9_000}
    ending = pd.DataFrame({"PRODUCT ALIAS": list(PRODUCTS), "12": [float("nan")] * PRODUCT_COUNT})
    for alias, qty in counts.items():
        ending.loc[ending["PRODUCT ALIAS"] == alias, "12"] = qty
    results, _staged, _errs = stage_batch_calculation(PRODUCTS, ending, engine, store, 2026, 12, date(2026, 8, 29))
    context = {"receipts": engine, "receipt_debug": debug, "control_counts": summarize_control_counts(results),
               "ending_supplied": len(counts),
               "exceptions": input_exceptions(None, debug) + product_exception_rows(results),
               "notes": ["Used the visible RECEIVED LOG."], "history": history or [],
               "run_signature": "abc123def456abcd7890"}
    data = export_master_excel(results, 12, fiscal_year=2026, context=context)
    return load_workbook(io.BytesIO(data)), results, context


def _strings(ws):
    return [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str)]


def test_sheet_order_puts_checks_first_and_keeps_every_material_sheet(make_store):
    wb, _results, _ = _run(make_store)
    assert wb.sheetnames[:5] == ["Checks", "Executive Summary", "Summary", "Period End Summary", "Receipts"]
    assert len(wb.sheetnames) == 5 + PRODUCT_COUNT
    assert all(ws.sheet_view.showGridLines is False for ws in wb.worksheets)
    assert len({n.lower() for n in wb.sheetnames}) == len(wb.sheetnames) and all(len(n) <= 31 for n in wb.sheetnames)


def test_checks_report_overall_status_tie_outs_and_the_late_receipt(make_store):
    wb, _results, _ = _run(make_store)
    assert any(v.startswith("Overall: PASS") for v in _strings(wb["Checks"]))
    rows = {r[0].value: r for r in wb["Checks"].iter_rows(min_row=6)}
    assert rows["Materials calculated"][1].value == PRODUCT_COUNT
    assert rows["Late receipts picked up"][1].value == 1
    assert rows["Ending counts supplied"][1].value == 1
    assert rows["Materials carried forward (no count)"][1].value == PRODUCT_COUNT - 1
    assert rows["Roll-forward ties: beginning + purchases - usage - ending"][2].value == "PASS"
    assert rows["Receipts sheet ties to purchases"][2].value == "PASS"


def test_checks_list_only_genuine_exceptions_and_say_none_when_clean(make_store):
    wb, _r, _c = _run(make_store)
    assert "None. Nothing needs a decision." in _strings(wb["Checks"])


def test_a_failing_material_makes_the_workpaper_fail_and_is_listed(make_store):
    wb, _results, _ = _run(make_store, counts={2: 9_000, 5: 500_000})      # material 5: a count with nothing behind it
    values = _strings(wb["Checks"])
    assert any(v.startswith("Overall: FAIL") for v in values)
    assert any("no beginning layers on file" in v for v in values)


def test_receipts_sheet_shows_each_receipt_as_reported_with_the_late_one_marked(make_store):
    wb, _results, _context = _run(make_store)
    ws = wb["Receipts"]
    rows = [r for r in ws.iter_rows(min_row=5, values_only=True) if isinstance(r[0], (date, datetime))]
    assert len(rows) == 2
    late = next(r for r in rows if r[1] == "Late")
    assert str(late[0])[:10] == "2026-07-31" and late[2] == 4 and late[5] == "CSI"
    assert late[8] == 9_000_000 and late[9] == 65_520.0 and late[10] == pytest.approx(65_520.0 / 9_000_000)
    assert late[11] == "P.12 FYE 2026"
    total_row = next(r for r in ws.iter_rows(min_row=5) if r[0].value == "TOTAL")
    assert str(total_row[9].value).startswith("=SUM(J")                    # a live total, not a pasted number


def test_period_end_summary_shows_closed_periods_and_this_one(make_store):
    history = [{"fiscal_year": 2026, "period": 11,
                "product_period_summary": {2: {"end_qty": 10_000, "end_val": 100.0, "purch_val": 0.0, "usage_val": 5.0}}},
               {"fiscal_year": 2025, "period": 12,
                "product_period_summary": {2: {"end_qty": 1, "end_val": 1.0, "purch_val": 1.0, "usage_val": 1.0}}}]
    wb, results, _ = _run(make_store, history=history)
    block = list(wb["Period End Summary"].iter_rows(min_row=4, max_row=4 + 3 + PRODUCT_COUNT, values_only=True))
    header = next(r for r in block if r[0] == "Alias")
    row = next(r for r in block if r[0] == 2)
    assert row[header.index("P11")] == 10_000                              # a closed period's saved figure
    assert row[header.index("P12")] == results[0]["metrics"]["end_qty"] == 9_000      # this period, calculated now
    assert row[header.index("P10")] is None                                # not processed: blank, never zero
    assert row[header.index("P01")] is None                                # the other fiscal year's P12 is not mixed in


def test_period_end_summary_has_category_totals_and_a_grand_total(make_store):
    wb, *_ = _run(make_store)
    labels = [c.value for row in wb["Period End Summary"].iter_rows(min_col=2, max_col=2) for c in row]
    for name in ("Preforms total", "Caps total", "Labels total", "Film total", "Glue total", "ALL MATERIALS"):
        assert name in labels


def test_a_carried_forward_material_is_marked_on_its_own_sheet(make_store):
    wb, _results, _ = _run(make_store)
    carried = next(n for n in wb.sheetnames if n.startswith("A06 "))
    assert "No ending count supplied" in str(wb[carried]["D2"].value)
    counted = next(n for n in wb.sheetnames if n.startswith("A02 "))
    assert wb[counted]["D2"].value is None


def test_without_a_context_the_report_is_what_it_was():
    from .test_controls_and_ingestion import _result
    from .conftest import layer as make_layer
    data = export_master_excel([_result([make_layer("2026-01-01", 100, 100.0)], [], 90, alias=2)], 3, fiscal_year=2026)
    names = load_workbook(io.BytesIO(data)).sheetnames
    assert "Checks" not in names and "Period End Summary" not in names


def test_checks_lists_the_unreadable_date_rows_for_a_person_to_scan(make_store):
    rows = [IN_P12, ["x", "Preforms", "AMCOR", "7/21/230", 1, "BOL", "SEAL", "628", 1_102_244, 37_277.89, None, None]]
    store = make_store(PRODUCTS)
    receipts, _e, _w, debug = prepare_period_receipts(
        _table(rows), MaterialMatcher(PRODUCTS, {}), P12, 12, material_columns=["ITEMS"], fiscal_year=2026,
        cutoff=CUTOFF, cutoff_period=11, cutoff_layers={})
    ending = pd.DataFrame({"PRODUCT ALIAS": list(PRODUCTS), "12": [float("nan")] * PRODUCT_COUNT})
    results, _s, _errs = stage_batch_calculation(PRODUCTS, ending, receipts, store, 2026, 12, date(2026, 8, 29))
    context = {"receipts": receipts, "receipt_debug": debug, "control_counts": summarize_control_counts(results),
               "ending_supplied": 0, "exceptions": [], "notes": [], "history": [], "run_signature": "x"}
    wb = load_workbook(io.BytesIO(export_master_excel(results, 12, fiscal_year=2026, context=context)))
    text = " | ".join(_strings(wb["Checks"]))
    assert "Old rows with unreadable dates, not used (1)" in text and "date as written '7/21/230'" in text
    assert "PO 628" in text and "cost 37277.89" in text
