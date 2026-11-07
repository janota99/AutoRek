"""Late and backdated entries: created after the period closed, and rows that changed between two pulls.

Both checks only report; they must never change a row, and a period nobody can prove is closed must not be flagged.
"""
from datetime import date

import pandas as pd

from apps.recon.late_entries import (
    ADDED, AMOUNT_CHANGED, DATE_CHANGED, PRIOR_YEAR, REMOVED, compare_pulls, detect_created_column,
    find_late_entries, to_dates,
)
from shared.fiscal_calendar import FiscalCalendar, default_calendar

CAL = default_calendar(2026)                           # 28-day periods from 2026-01-01: P01 ends Jan 28, P02 Feb 25
MAPPING = {"po": "PO", "invoice": "Invoice", "amount": "Amount", "date": "Date", "customer": "Customer"}


def _frame(rows):
    return pd.DataFrame(rows, columns=["PO", "Invoice", "Amount", "Date", "Created", "Customer"])


def _late(rows, **kw):
    args = dict(dataset="QuickBooks", mapping=MAPPING, created_column="Created", calendar=CAL, selected_period=3)
    args.update(kw)
    return find_late_entries(_frame(rows), **args)


def test_a_row_created_after_its_period_closed_is_flagged():
    result = _late([
        ("PO1", "I1", "100.00", "2026-01-20", "2026-03-02", "Acme"),   # P01 ended Jan 28; created Mar 2: late
        ("PO2", "I2", "50.00", "2026-01-20", "2026-01-30", "Acme"),    # created 2 days after the end: inside close days
        ("PO3", "I3", "75.00", "2026-03-05", "2026-03-06", "Acme"),    # P03 is the selected period: not closed
    ])
    assert list(result.rows["Invoice"]) == ["I1"]
    assert result.rows.iloc[0]["Transaction period"] == "P01" and result.rows.iloc[0]["Days after period end"] == 33
    assert result.status == "WARNING" and result.amount_cents == 10000 and "$100.00" in result.details


def test_close_days_are_a_visible_setting_not_a_hidden_rule():
    rows = [("PO1", "I1", "10.00", "2026-01-20", "2026-02-03", "A")]          # 6 days after P01 ended
    assert len(_late(rows, close_days=5).rows) == 1
    assert _late(rows, close_days=6).rows.empty and "6 close day(s)" in _late(rows, close_days=6).details


def test_without_a_selected_period_every_closed_period_is_checked():
    rows = [("PO1", "I1", "10.00", "2026-02-10", "2026-04-01", "A")]
    assert len(_late(rows, selected_period=None).rows) == 1
    assert _late(rows, selected_period=2).rows.empty                          # P02 is the selected, still-open period


def test_prior_fiscal_year_rows_use_the_latest_possible_end():
    late = _late([("PO1", "I1", "10.00", "2025-11-10", "2026-01-20", "A")])   # created 20 days after Dec 31
    assert list(late.rows["Transaction period"]) == [PRIOR_YEAR]
    early = _late([("PO1", "I1", "10.00", "2025-11-10", "2026-01-03", "A")])  # within close days of Dec 31
    assert early.rows.empty


def test_unreadable_dates_are_counted_and_never_flagged():
    result = _late([("PO1", "I1", "10.00", "not a date", "2026-04-01", "A"),
                    ("PO2", "I2", "10.00", "2026-01-10", "", "A")])
    assert result.rows.empty and result.unreadable_dates == 1 and result.unreadable_created == 1
    assert result.status == "WARNING" and "could not be read" in result.details


def test_missing_columns_say_what_to_map_instead_of_passing():
    assert "created-date" in _late([], created_column=None).note
    assert "transaction date" in _late([], mapping={**MAPPING, "date": None}).note
    broken = FiscalCalendar(2026, date(2026, 1, 1), CAL.period_ends[:12])
    assert "not usable" in _late([], calendar=broken).note


def test_excel_serial_dates_and_created_columns_are_read():
    assert to_dates(pd.Series([46023, "2026-01-02", pd.Timestamp("2026-01-03 14:30")])).dt.date.tolist() == [
        date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    assert detect_created_column(["Num", "Date Created", "Amount"]) == "Date Created"
    assert detect_created_column(["Num", "Amount"]) is None


def test_the_check_does_not_change_the_frame():
    frame = _frame([("PO1", "I1", "100.00", "2026-01-20", "2026-03-02", "Acme")])
    before = frame.copy(deep=True)
    find_late_entries(frame, dataset="QuickBooks", mapping=MAPPING, created_column="Created", calendar=CAL,
                      selected_period=3)
    pd.testing.assert_frame_equal(frame, before)


# --- two pulls -------------------------------------------------------------------------------------------------------
def _pull(rows):
    return pd.DataFrame(rows, columns=["PO", "Invoice", "Amount", "Date", "Customer", "Created"])


def _diff(old, new):
    return compare_pulls(_pull(old), _pull(new), mapping=MAPPING, created_column="Created")


BASE = [("PO1", "I1", "100.00", "2026-01-05", "Acme", "2026-01-05"),
        ("PO2", "I2", "250.50", "2026-01-06", "Acme", "2026-01-06")]


def test_identical_pulls_have_no_findings():
    result = _diff(BASE, BASE)
    assert result.rows.empty and result.status == "PASS" and result.drift_cents == 0
    assert result.counts["Unchanged"] == 2 and "same in both" in result.details


def test_a_backdated_row_appears_as_added_with_its_created_date_and_moves_the_total():
    new = BASE + [("PO3", "I3", "75.25", "2026-01-07", "Bolt", "2026-02-10")]
    result = _diff(BASE, new)
    assert list(result.rows["Change"]) == [ADDED] and result.rows.iloc[0]["Created"] == date(2026, 2, 10)
    assert result.drift_cents == 7525 and "+$75.25" in result.details and result.status == "WARNING"


def test_a_row_that_disappears_is_listed():
    result = _diff(BASE, BASE[:1])
    assert list(result.rows["Change"]) == [REMOVED] and result.drift_cents == -25050


def test_a_single_leftover_pair_is_an_amount_change_but_an_ambiguous_group_is_not_paired():
    changed = _diff(BASE, [BASE[0], ("PO2", "I2", "300.00", "2026-01-06", "Acme", "2026-02-01")])
    row = changed.rows.iloc[0]
    assert list(changed.rows["Change"]) == [AMOUNT_CHANGED] and (row["Earlier amount"], row["Current amount"]) == (250.5, 300.0)
    old = BASE + [("PO2", "I2", "10.00", "2026-01-06", "Acme", "2026-01-06")]
    ambiguous = _diff(old, [BASE[0], ("PO2", "I2", "99.00", "2026-01-06", "Acme", "2026-02-01")])
    assert sorted(ambiguous.rows["Change"]) == sorted([REMOVED, REMOVED, ADDED])    # 2 old leftovers, 1 new: no guess


def test_a_moved_transaction_date_is_reported_for_an_otherwise_identical_row():
    result = _diff(BASE, [BASE[0], ("PO2", "I2", "250.50", "2026-02-02", "Acme", "2026-01-06")])
    row = result.rows.iloc[0]
    assert list(result.rows["Change"]) == [DATE_CHANGED]
    assert (row["Earlier date"], row["Current date"]) == (date(2026, 1, 6), date(2026, 2, 2)) and result.drift_cents == 0


def test_repeated_identical_lines_are_counted_not_collapsed():
    twice = BASE + [BASE[0]]
    result = _diff(BASE, twice)
    assert list(result.rows["Change"]) == [ADDED] and result.drift_cents == 10000


def test_differently_formatted_identifiers_still_pair():
    result = _diff([("PO-1", "i 1", "100.00", "2026-01-05", "A", None)], [("po1", "I1", "$100.00", "2026-01-05", "A", None)])
    assert result.rows.empty


def test_pulls_without_the_mapped_columns_are_not_compared():
    result = compare_pulls(pd.DataFrame({"X": [1]}), _pull(BASE), mapping=MAPPING)
    assert result.status == "WARNING" and "not compared" in result.details
