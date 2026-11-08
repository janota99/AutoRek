"""The recurring "Raw Goods & Receivings" workbook: one file, both sources, found by sheet and header names."""
import io
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import Workbook

from apps.fifo_inventory.fifo_calculations import calculate_fifo, stage_batch_calculation
from apps.fifo_inventory.intake import (
    PeriodWindow, clamp_late_receipts, period_window, product_exception_rows, receipt_register_counts,
    register_entries,
)
from apps.fifo_inventory.materials import MaterialMatcher
from apps.fifo_inventory.user_inputs import PRODUCTS
from apps.fifo_inventory.workbook_import import (
    NONE_CHOICE, list_sheets, plan_sheets, read_operations_workbook, read_received_log,
)
from shared.fiscal_calendar import FiscalCalendar

from .conftest import AS_OF, layer

PERIOD = 12
FY = 2026
CALENDAR = FiscalCalendar(2026, date(2025, 9, 28), (
    date(2025, 10, 25), date(2025, 11, 22), date(2025, 12, 20), date(2026, 1, 17), date(2026, 2, 14),
    date(2026, 3, 14), date(2026, 4, 11), date(2026, 5, 9), date(2026, 6, 6), date(2026, 7, 4),
    date(2026, 8, 1), date(2026, 8, 29), date(2026, 10, 3)))
WINDOW = period_window(FY, PERIOD, CALENDAR, date(2026, 8, 29))      # Aug 2 - Aug 29, 2026

LOG_HEADER = ["DESCRIPTION", "ITEMS", "COMPANY", "DATE DELIVERED", "LOT #", "BOL #", "SEAL #/Trlr", "PO #",
              "QUANTITY", "PRICE", "PERIOD", "INVOICE #"]
LABEL = "P.12 FYE 2026"


def _log_row(item, day, qty, price, period=LABEL, invoice="Inv#1", po="PO1", vendor="Plastipak"):
    return [vendor, item, vendor.upper(), day, 1, "BOL", "SEAL", po, qty, price, period, invoice]


def _period_end_rows(counts):
    """The first table: heading rows, supplies, and counts for periods 1-13 (only P11 and P12 filled)."""
    rows = [["Vendor", "Material", None, None] + [f"Period {n}" for n in range(1, 14)]]
    rows.append([None, None, "PLASTIPAK- PREFORMS -8.5g", "each"] + [None] * 10 + [counts.get("pre11"), counts.get("pre12"), None])
    rows.append([None, None, "Blue Caps", "each"] + [None] * 10 + [3_400_000, 3_400_000, None])
    rows.append([None, None, "Natural Caps-SSB", "each"] + [None] * 10 + [0, 0, None])
    rows.append([None, None, "CSI Caps", "each"] + [None] * 10 + [10_900_000, counts.get("csi12"), None])
    rows.append([None, None, "LABELS", None] + [None] * 13)                              # section heading
    rows.append(["Amcor", "Labels", "Panhandle Pure", "each"] + [None] * 10 + [18_000_000, counts.get("pp12"), None])
    rows.append([None, None, "Pallet Wrap (24)", "roll"] + [None] * 10 + [4, 5, None])    # a supply, with counts
    rows.append([None, None, "Slip Sheets", "each"] + [None] * 13)
    rows.append([None] * 17)                                                               # the table ends
    rows.append([None, None, "MIN'S BASED OFF 12,000,000", None] + [None] * 13)
    rows.append([None, None, "PP 24 ", "Cases"] + [None] * 10 + [9, 9, None])             # a second table: not inventory
    return rows


def _workbook(log_rows, counts=None, *, extra_sheets=(), log_name="RECEIVED LOG", hidden_stale=True,
              period_end_name="PERIOD END 2026", header=None):
    wb = Workbook()
    wb.active.title = "FINISHED GOODS INVENTORY PRINT"
    if hidden_stale:
        stale = wb.create_sheet("RECEIVED")
        stale.append(LOG_HEADER)
        stale.append(_log_row("preforms", datetime(2026, 8, 5), 999, 9.0))
        stale.sheet_state = "hidden"
    log = wb.create_sheet(log_name)
    log.append(header or LOG_HEADER)
    for row in log_rows:
        log.append(row)
    pe = wb.create_sheet(period_end_name)
    pe.append([None, None, "Description", None] + [datetime(2025, 10, 25)] * 13)
    for row in _period_end_rows(counts or {}):
        pe.append(row)
    stale_pe = wb.create_sheet("PERIOD END 20261")
    stale_pe.sheet_state = "hidden"
    for name, state in extra_sheets:
        sheet = wb.create_sheet(name)
        sheet.sheet_state = state
        sheet.append(LOG_HEADER)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


@pytest.fixture
def matcher():
    return MaterialMatcher(PRODUCTS, {})


def _read(data, window=WINDOW, period=PERIOD, **kwargs):
    return read_operations_workbook(data, FY, period, MaterialMatcher(PRODUCTS, {}), window, **kwargs)


GOOD_ROWS = [
    _log_row("Preforms", datetime(2026, 8, 4), 1_695_168, 43_125.07, invoice="Inv#9001", po="3653"),
    _log_row("Preforms", datetime(2026, 8, 15), 1_695_168, 42_023.21, invoice="Inv#9002", po="3671"),
    _log_row("CLOSURES", datetime(2026, 8, 5), 9_000_000, 65_520.00, invoice="Inv#9003", vendor="CSI"),
]
COUNTS = {"pre11": 10_076_832, "pre12": 5_430_816, "csi12": 26_700_000, "pp12": 51_500_460}


# --- one workbook, both sources ------------------------------------------------------------------

def test_one_workbook_fills_both_the_receipts_and_the_ending_quantities():
    result = _read(_workbook(GOOD_ROWS, COUNTS))
    assert result.problems == [] and result.receipt_errors == []
    assert result.receipts["PRODUCT ALIAS"].tolist() == [2, 2, 4]
    assert result.receipts["QUANTITY"].tolist() == [1_695_168, 1_695_168, 9_000_000]
    assert result.ending.errors == []
    counts = result.ending.grid.set_index("PRODUCT ALIAS")["12"]
    assert counts[2] == 5_430_816 and counts[5] == 51_500_460


def test_the_price_column_is_the_receipts_total_and_unit_cost_is_total_over_quantity():
    result = _read(_workbook(GOOD_ROWS, COUNTS))
    row = result.receipts.iloc[0]
    assert row["PRICE"] == pytest.approx(43_125.07) and row["PO #"] == "3653" and row["INVOICE #"] == "Inv#9001"
    _on_hand, _dep, raw, _m = calculate_fifo([], result.receipts[result.receipts["PRODUCT ALIAS"] == 2], 3_390_336,
                                              period=12, fiscal_year=2026, as_of_date=AS_OF)
    assert raw[0]["unit_cost"] == pytest.approx(43_125.07 / 1_695_168, rel=1e-12)


def test_the_material_is_the_items_column_not_the_description_column():
    rows = [_log_row("Preforms", datetime(2026, 8, 4), 100, 5.0)]
    rows[0][0] = "Blue Caps"                     # DESCRIPTION holds a vendor/category and must not decide it
    assert _read(_workbook(rows, COUNTS)).receipts["PRODUCT ALIAS"].tolist() == [2]


def test_natural_and_csi_caps_are_one_material_and_say_so():
    result = _read(_workbook(GOOD_ROWS, COUNTS))
    assert result.ending.grid.set_index("PRODUCT ALIAS")["12"][4] == 26_700_000
    assert any("Natural Caps-SSB 0 + CSI Caps 26,700,000" in n for n in result.notes)
    assert result.ending.duplicates == {}


def test_headings_supplies_and_the_tables_below_the_first_are_not_exceptions():
    result = _read(_workbook(GOOD_ROWS, COUNTS))
    assert result.ending.errors == [] and result.ending.unmapped == []
    assert any("non-inventory supply line" in n for n in result.notes)
    assert any("Only the first table" in n for n in result.notes)
    assert [u for u in result.ending.unmapped if "PP 24" in u["name"]] == []        # the cases table was never read


def test_materials_without_a_count_carry_forward_without_exceptions():
    result = _read(_workbook(GOOD_ROWS, COUNTS))
    uncounted = {name for _a, name in result.ending.missing}
    assert "FOOD KING LABELS" in uncounted and "W0082 - BLUE CAPS" not in uncounted
    assert result.ending.errors == [] and result.problems == []


# --- the fiscal calendar and column K --------------------------------------------------------------

def test_history_and_other_periods_in_the_log_are_not_reimported():
    rows = GOOD_ROWS + [_log_row("Preforms", datetime(2025, 3, 3), 50, 2.0, period="P.5 FYE 2025"),
                        _log_row("Preforms", datetime(2026, 9, 10), 60, 3.0, period="P.13 FYE 2026")]
    result = _read(_workbook(rows, COUNTS))
    assert len(result.receipts) == 3 and result.receipt_errors == []
    assert any("earlier periods" in n for n in result.notes) and any("later periods" in n for n in result.notes)


def test_two_identical_looking_deliveries_with_different_references_are_both_kept():
    rows = [_log_row("Preforms", datetime(2026, 8, 15), 1_695_168, 42_023.21, invoice="Inv#1", po="3671"),
            _log_row("Preforms", datetime(2026, 8, 15), 1_695_168, 42_023.21, invoice="Inv#2", po="3657")]
    result = _read(_workbook(rows, COUNTS), known_receipts={})
    assert len(result.receipts) == 2 and not any("may be duplicates" in w for w in result.receipt_warnings)


def test_a_row_labelled_for_this_period_but_dated_before_it_is_recognized_now_and_flagged():
    rows = GOOD_ROWS + [_log_row("CLOSURES", datetime(2026, 7, 31), 9_000_000, 65_520.0, invoice="Inv#late", vendor="CSI")]
    result = _read(_workbook(rows, COUNTS))
    assert len(result.receipts) == 4 and result.receipt_errors == []
    late = result.receipts[result.receipts["RECEIPT STATUS"] == "late"]
    assert late["PRODUCT ALIAS"].tolist() == [4] and late["REPORTED PERIOD"].tolist() == [LABEL]
    info = result.receipt_debug["late_rows"].iloc[0]
    assert str(info["DELIVERED"]) == "2026-07-31" and info["REPORTED PERIOD"] == LABEL
    assert not any("dated outside" in w for w in result.receipt_warnings)          # informational, not a warning
    assert any("late receipt(s) were never processed before" in n for n in result.notes)


def test_an_old_years_period_label_is_history_not_a_mistake():
    rows = GOOD_ROWS + [_log_row("Preforms", datetime(2022, 8, 8), 5, 1.0, period="P.12")]
    result = _read(_workbook(rows, COUNTS))
    assert not any("labelled for period" in w for w in result.receipt_warnings)


def test_a_label_that_disagrees_with_the_date_warns_and_the_date_wins():
    rows = [_log_row("Preforms", datetime(2026, 8, 4), 10, 1.0, period="P.11 FYE 2026")]
    result = _read(_workbook(rows, COUNTS))
    assert len(result.receipts) == 1 and any("differs from the period" in w for w in result.receipt_warnings)


def test_a_row_with_an_unreadable_date_is_history_unless_its_label_names_this_period():
    rows = GOOD_ROWS + [
        _log_row("Preforms", "6/28/222", 58, None, period=None),                 # old junk: noted, never an exception
        _log_row("Preforms", "8/9/26x", 70, 7.0, period=LABEL),                   # claims this period: must be fixed
    ]
    result = _read(_workbook(rows, COUNTS))
    assert any("no readable delivery date" in n for n in result.notes)
    assert len(result.receipt_debug["unplaced_rows"]) == 1
    assert any("Delivery date is missing" in r for r in result.receipt_debug["rejected_rows"]["REASON"])


def test_a_blank_or_text_cost_inside_the_period_is_a_row_error_and_zero_is_used():
    rows = [_log_row("Preforms", datetime(2026, 8, 4), 10, None, invoice="a"),
            _log_row("Preforms", datetime(2026, 8, 5), 10, "TBD", invoice="b"),
            _log_row("Preforms", datetime(2026, 8, 6), 10, 0, invoice="c")]
    result = _read(_workbook(rows, COUNTS))
    reasons = " | ".join(result.receipt_debug["rejected_rows"]["REASON"])
    assert "Cost is missing" in reasons and "Cost is not a number: 'TBD'" in reasons
    assert result.receipts["PRICE"].tolist() == [0.0]
    assert any("$0.00 (entered as zero, not blank)" in w for w in result.receipt_warnings)


def test_a_repeated_invoice_for_one_material_warns_without_changing_any_cost():
    rows = [_log_row("Preforms", datetime(2026, 8, 4), 10, 5.0, invoice="Inv#7", po="1"),
            _log_row("Preforms", datetime(2026, 8, 5), 12, 6.0, invoice="Inv#7", po="2")]
    result = _read(_workbook(rows, COUNTS))
    assert result.receipts["PRICE"].tolist() == [5.0, 6.0]
    assert any("same invoice reference" in w for w in result.receipt_warnings)


def test_a_non_inventory_receipt_is_ignored_but_an_unknown_material_is_a_row_error():
    rows = GOOD_ROWS + [_log_row("paper towels etc.", datetime(2026, 8, 6), 5, 20.0),
                        _log_row("Mystery Resin", datetime(2026, 8, 7), 5, 20.0)]
    result = _read(_workbook(rows, COUNTS))
    assert any("non-inventory supply row" in n for n in result.notes)
    assert "Mystery Resin" in result.receipt_debug["unmapped"] and len(result.receipts) == 3


# --- sheets and headers: explicit, never guessed ----------------------------------------------------

def test_a_hidden_duplicate_beside_one_visible_sheet_is_skipped_with_a_note():
    result = _read(_workbook(GOOD_ROWS, COUNTS))
    assert result.plan.received == "RECEIVED LOG" and result.plan.period_end == "PERIOD END 2026"
    assert any("ignored hidden 'RECEIVED'" in n for n in result.notes)
    assert any("'PERIOD END 20261'" in n for n in result.notes)


def test_the_older_sheet_name_received_is_accepted_when_it_is_the_only_one():
    data = _workbook(GOOD_ROWS, COUNTS, log_name="RECEIVED", hidden_stale=False)
    plan = plan_sheets(list_sheets(data), FY)
    assert plan.received == "RECEIVED" and plan.problems == []


def test_two_visible_candidates_are_a_problem_not_a_choice():
    data = _workbook(GOOD_ROWS, COUNTS, extra_sheets=[("RECEIVED", "visible")], hidden_stale=False)
    plan = plan_sheets(list_sheets(data), FY)
    assert plan.received is None and any("More than one sheet could be the received log" in p for p in plan.problems)
    chosen = plan_sheets(list_sheets(data), FY, received_sheet="RECEIVED LOG")
    assert chosen.received == "RECEIVED LOG" and chosen.problems == []


def test_an_unexpected_sheet_name_lists_what_was_found():
    data = _workbook(GOOD_ROWS, COUNTS, log_name="Deliveries", hidden_stale=False, period_end_name="PERIOD END 2025B")
    plan = plan_sheets(list_sheets(data), FY)
    text = " | ".join(plan.problems)
    assert "No sheet named 'RECEIVED LOG'" in text and "'Deliveries'" in text
    assert "No 'PERIOD END 2026' sheet" in text and "'PERIOD END 2025B'" in text
    result = _read(data)
    assert result.receipts.empty and result.ending is None and result.problems


def test_another_fiscal_years_period_end_sheet_is_never_used_for_this_year():
    plan = plan_sheets(list_sheets(_workbook(GOOD_ROWS, COUNTS, period_end_name="PERIOD END 2025")), FY)
    assert plan.period_end is None and plan.problems


def test_choosing_none_skips_a_source_on_purpose():
    data = _workbook(GOOD_ROWS, COUNTS)
    result = _read(data, received_sheet=NONE_CHOICE)
    assert result.receipts.empty and result.ending is not None and result.problems == []
    assert any("No received log was used" in n for n in result.notes)


def test_a_duplicated_price_header_is_an_error_naming_both_columns():
    header = LOG_HEADER[:10] + ["PRICE"] + LOG_HEADER[11:]
    table, errors, _notes = read_received_log(_workbook([], COUNTS, header=header), "RECEIVED LOG")
    assert table is None and "more than one 'PRICE' column (J, K)" in errors[0]


def test_a_missing_required_header_is_named():
    header = [h if h != "QUANTITY" else "QTY IN" for h in LOG_HEADER]
    table, errors, _notes = read_received_log(_workbook([], COUNTS, header=header), "RECEIVED LOG")
    assert table is None and errors


def test_moved_columns_are_found_by_header_with_a_note():
    header = ["EXTRA"] + LOG_HEADER[:12]
    shifted = [["x"] + row for row in GOOD_ROWS]
    table, errors, notes = read_received_log(_workbook(shifted, COUNTS, header=header), "RECEIVED LOG")
    assert errors == [] and any("differ from the usual layout" in n for n in notes)


def test_a_wrong_period_end_column_header_is_not_guessed():
    data = _workbook(GOOD_ROWS, COUNTS)
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data))
    sheet = wb["PERIOD END 2026"]
    sheet["P2"] = "Period 11"                        # two columns now claim to be period 11; period 12 has none
    out = io.BytesIO()
    wb.save(out)
    result = read_operations_workbook(out.getvalue(), FY, PERIOD, MaterialMatcher(PRODUCTS, {}), WINDOW)
    assert result.ending.structural and "no 'Period 12' column" in result.ending.errors[0]


# --- through the engine: the continuous ledger -----------------------------------------------------

def test_the_workbook_drives_the_ledger_end_to_end(make_store):
    store = make_store(PRODUCTS)
    store.set(2, [layer("2026-07-20", 10_076_832, 256_354.58)])
    store.set(3, [layer("2026-01-05", 3_400_000, 25_563.09)])
    store.set(4, [layer("2026-07-21", 10_900_000, 77_224.73)])
    result = _read(_workbook(GOOD_ROWS, {**COUNTS, "csi12": 15_000_000}))
    results, staged, errors = stage_batch_calculation(PRODUCTS, result.ending.grid, result.receipts, store,
                                                      FY, PERIOD, date(2026, 8, 29))
    assert errors == []
    by_alias = {r["alias"]: r for r in results}
    pre = by_alias[2]["metrics"]
    assert pre["purch_qty"] == 3_390_336 and pre["purch_val"] == pytest.approx(85_148.28)
    assert pre["usage_qty"] == 10_076_832 + 3_390_336 - 5_430_816
    caps = by_alias[4]["metrics"]
    assert caps["usage_qty"] == 10_900_000 + 9_000_000 - 15_000_000 and caps["purch_val"] == pytest.approx(65_520.0)
    blue = by_alias[3]["metrics"]
    assert blue["usage_qty"] == pytest.approx(0) and blue["end_qty"] == 3_400_000        # counted, unchanged
    carried = by_alias[7]["metrics"]
    assert carried["end_qty"] == 0 and carried["usage_qty"] == pytest.approx(0)           # no layers, no count
    assert not [r for r in product_exception_rows(results) if r["Item"].startswith(("3 ", "7 "))]


# --- the real workbook, when it is on this machine -------------------------------------------------

REAL = Path.home() / "Downloads" / "Raw Goods & Receivings (8).xlsx"


@pytest.mark.skipif(not REAL.exists(), reason="operations' workbook is not on this machine")
def test_the_real_workbook_for_period_12_ties_to_the_source_sheets():
    result = _read(REAL.read_bytes())
    assert result.problems == [] and result.receipt_errors == [] and result.ending.errors == []
    receipts = result.receipts
    # Preforms and labels as in the period; caps include the CSI row dated 7/31 but labelled P.12, recognized late.
    assert receipts.groupby("PRODUCT ALIAS")["QUANTITY"].sum().to_dict() == {2: 15_256_512, 4: 36_000_000, 5: 42_708_600}
    assert receipts.groupby("PRODUCT ALIAS")["PRICE"].sum().round(2).to_dict() == {2: 382_616.33, 4: 264_048.64, 5: 105_274.24}
    late = result.receipt_debug["late_rows"]
    assert len(late) == 1 and str(late.iloc[0]["DELIVERED"]) == "2026-07-31"
    grid = result.ending.grid.set_index("PRODUCT ALIAS")["12"]
    assert grid.notna().sum() == len(PRODUCTS) and grid[2] == 5_430_816 and grid[4] == 26_700_000 and grid[18] == 0


@pytest.mark.skipif(not REAL.exists(), reason="operations' workbook is not on this machine")
def test_the_real_p12_receipt_total_is_reproduced_with_the_late_receipt_rule():
    result = _read(REAL.read_bytes())
    assert round(float(result.receipts["PRICE"].sum()), 2) == 751_939.21
    assert len(result.receipts) == 15 and (result.receipts["RECEIPT STATUS"] == "late").sum() == 1


@pytest.mark.skipif(not REAL.exists(), reason="operations' workbook is not on this machine")
def test_the_real_log_over_consecutive_periods_omits_nothing_and_duplicates_nothing():
    """P11, P12, P13 against the real log with a register carried between them: every valid receipt after the cutoff
    (or labelled for these periods) is recognized in exactly one period, judged against an independent read of the sheet."""
    import re
    from openpyxl import load_workbook
    from apps.fifo_inventory.intake import extract_period, receipt_register_counts, register_entries

    cutoff = date(2026, 7, 4)
    data = REAL.read_bytes()
    recognized_rows, register = [], []
    for period in (11, 12, 13):
        window = period_window(FY, period, CALENDAR, CALENDAR.period_ends[period - 1])
        known = receipt_register_counts(register)
        result = read_operations_workbook(data, FY, period, MaterialMatcher(PRODUCTS, {}), window, known_receipts=known,
                                          cutoff=cutoff, cutoff_period=10, cutoff_layers={})
        assert result.receipt_errors == [] and result.receipt_debug["review_rows"].empty
        recognized_rows += result.receipts["_SOURCE_ROW"].astype(int).tolist()
        register += register_entries(result.receipts, f"FY2026-P{period:02d}")
    assert len(recognized_rows) == len(set(recognized_rows)), "a receipt was recognized twice"

    ws = load_workbook(io.BytesIO(data), data_only=True)["RECEIVED LOG"]
    matcher = MaterialMatcher(PRODUCTS, {})
    expected = set()
    for number, row in enumerate(ws.iter_rows(min_row=2, max_col=12, values_only=True), start=2):
        item, delivered, qty, price, label = row[1], row[3], row[8], row[9], row[10]
        if not (isinstance(delivered, datetime) and isinstance(qty, (int, float)) and isinstance(price, (int, float))):
            continue
        if matcher.match(item).alias is None:
            continue
        in_periods = cutoff < delivered.date() <= CALENDAR.period_ends[12]
        labelled = (label and "FYE 2026" in str(label) and extract_period(label) in (11, 12, 13)
                    and delivered.date() <= cutoff)
        if in_periods or labelled:
            expected.add(number)
    assert set(recognized_rows) == expected, f"omitted {sorted(expected - set(recognized_rows))}, extra {sorted(set(recognized_rows) - expected)}"
    assert len(expected) >= 15


# --- the fiscal calendar against the workbook's own period-end dates ----------------------------------------------

from datetime import timedelta  # noqa: E402
from shared.fiscal_calendar import locate, validate  # noqa: E402


def _calendar_for(year, start, ends):
    return FiscalCalendar(year, start, tuple(date.fromisoformat(d) for d in ends))


FY2027 = _calendar_for(2027, date(2026, 10, 4), [
    "2026-10-31", "2026-11-28", "2026-12-26", "2027-01-23", "2027-02-20", "2027-03-20", "2027-04-17",
    "2027-05-15", "2027-06-12", "2027-07-10", "2027-08-07", "2027-09-04", "2027-10-02"])


def test_the_fiscal_calendars_are_valid_and_contiguous():
    assert validate(CALENDAR) is None and validate(FY2027) is None
    for calendar in (CALENDAR, FY2027):
        starts = [calendar.start] + [e + timedelta(days=1) for e in calendar.period_ends[:-1]]
        gaps = [(e - s).days + 1 for s, e in zip(starts, calendar.period_ends)]
        assert gaps[:12] == [28] * 12                                  # thirteen four-week periods...
    assert [(e - s).days + 1 for s, e in zip([CALENDAR.start] + [e + timedelta(days=1) for e in CALENDAR.period_ends[:-1]],
                                              CALENDAR.period_ends)][12] == 35      # ...FY2026's last runs five weeks
    assert [(e - s).days + 1 for s, e in zip([FY2027.start] + [e + timedelta(days=1) for e in FY2027.period_ends[:-1]],
                                              FY2027.period_ends)][12] == 28
    assert CALENDAR.period_ends[-1] + timedelta(days=1) == FY2027.start      # FY2026 ends the day before FY2027 starts
    assert locate(CALENDAR, date(2026, 8, 29)) == 12 and locate(FY2027, date(2026, 10, 4)) == 1


def test_a_year_typo_in_the_workbook_is_noted_and_the_calendar_is_used():
    # FY2026 Period 1 in operations' sheet reads 10/25/2026; every other date and the calendar say 10/25/2025.
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(_workbook(GOOD_ROWS, COUNTS)))
    sheet = wb["PERIOD END 2026"]
    for column, end in enumerate(CALENDAR.period_ends, start=5):
        sheet.cell(row=1, column=column, value=datetime(end.year, end.month, end.day))
    sheet.cell(row=1, column=5, value=datetime(2026, 10, 25))          # the typo: should be 2025-10-25
    buffer = io.BytesIO()
    wb.save(buffer)
    data = buffer.getvalue()
    window = period_window(FY, 1, CALENDAR, CALENDAR.period_ends[0])
    p1 = read_operations_workbook(data, FY, 1, MaterialMatcher(PRODUCTS, {}), window, calendar=CALENDAR)
    assert not any("disagree" in e for e in p1.ending.errors)
    assert any("year typo in the workbook" in n for n in p1.ending.notes)
    p12 = read_operations_workbook(data, FY, PERIOD, MaterialMatcher(PRODUCTS, {}), WINDOW, calendar=CALENDAR)
    assert p12.ending.errors == [] and p12.ending.warnings == [] and not any("typo" in n for n in p12.ending.notes)


def test_a_calendar_that_disagrees_with_the_workbook_blocks_the_period_it_would_misplace():
    from shared.fiscal_calendar import default_calendar
    wrong = default_calendar(2026, date(2026, 1, 1))                   # what the app starts with before a calendar is set
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(_workbook(GOOD_ROWS, COUNTS)))
    sheet = wb["PERIOD END 2026"]
    for column, end in enumerate(CALENDAR.period_ends, start=5):
        sheet.cell(row=1, column=column, value=datetime(end.year, end.month, end.day))
    buffer = io.BytesIO()
    wb.save(buffer)
    window = period_window(FY, PERIOD, wrong, wrong.period_ends[11])
    result = read_operations_workbook(buffer.getvalue(), FY, PERIOD, MaterialMatcher(PRODUCTS, {}), window, calendar=wrong)
    assert any("disagree about Period 12" in e for e in result.ending.errors)
    assert "Workspace" in result.ending.errors[0]
