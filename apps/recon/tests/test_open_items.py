"""Review aids for unresolved items: same amount on the other side, age, and concentration.

They describe what a run left open; they must never create or imply a match, and a run's results must be
identical with or without them.
"""
import pandas as pd
import pytest

from apps.recon.duplicates import AMOUNT_CENTS
from apps.recon.matching.core import FISCAL_LABEL
from apps.recon.matching.open_items import (
    SAME_AMOUNT_NOTE, SECTION_AGE, SECTION_CUSTOMER, SECTION_SAME_AMOUNT, build_open_item_insights,
)
from apps.recon.tests.test_dispositions import mixed_result  # noqa: F401  (the shared fixture)


def _frame(rows):
    return pd.DataFrame(rows, columns=["Customer", AMOUNT_CENTS, FISCAL_LABEL])


QB = _frame([
    ("Acme", 350570, "P10-2026"),     # 0: same amount as an unresolved Infinium row
    ("Acme", 350570, "P09-2026"),     # 1: same amount again (one Infinium row may serve several: no pairing is made)
    ("Bolt", 12345, "P07-2026"),      # 2: no same-amount row
    ("Bolt", -5000, "P10-2026"),      # 3: a credit
    ("", 0, "Unspecified"),           # 4: zero amount, no customer, no period
])
INF = _frame([("", 350570, "P10-2026"), ("", 99999, "P10-2026")])


def _insights(**overrides):
    args = dict(fiscal_period=10, fiscal_year=2026, customer_col="Customer")
    args.update(overrides)
    return build_open_item_insights(QB, INF, [0, 1, 2, 3, 4], [0, 1], **args)


def _section(frame, name):
    return frame[frame["Section"] == name].set_index("Item")


def test_same_amount_rows_are_counted_without_pairing_anything():
    rows = _section(_insights(), SECTION_SAME_AMOUNT)
    with_match = rows.loc["Unmatched QuickBooks rows with an unresolved Infinium row at the same amount"]
    without = rows.loc["Unmatched QuickBooks rows with no unresolved Infinium row at that amount"]
    assert (with_match["Rows"], with_match["Amount"]) == (2, 7011.40)         # both Acme rows see the one 3,505.70 row
    assert (without["Rows"], without["Amount"]) == (2, 123.45 - 50.00)       # zero amounts are left out of both
    assert with_match["Note"] == SAME_AMOUNT_NOTE and "not evidence" in SAME_AMOUNT_NOTE
    inf_side = rows.loc["Unmatched Infinium rows with an unresolved QuickBooks row at the same amount"]
    assert inf_side["Rows"] == 1 and inf_side["Amount"] == 3505.70


def test_age_is_counted_in_fiscal_periods_back_from_the_selected_period():
    rows = _section(_insights(), SECTION_AGE)
    assert rows.loc["Current period", "Rows"] == 2                              # P10 twice
    assert rows.loc["1 period old", "Rows"] == 1                                # P09
    assert rows.loc["3 or more periods old", "Rows"] == 1                       # P07
    assert rows.loc["Period not stated", "Rows"] == 1
    assert rows["Share of exceptions"].dropna().sum() == pytest.approx(1.0)


def test_age_crosses_a_fiscal_year_boundary():
    qb = _frame([("A", 100, "P13-2025"), ("A", 100, "P01-2026")])
    rows = _section(build_open_item_insights(qb, INF, [0, 1], [], fiscal_period=1, fiscal_year=2026,
                                             customer_col="Customer"), SECTION_AGE)
    assert rows.loc["Current period", "Rows"] == 1 and rows.loc["1 period old", "Rows"] == 1


def test_without_a_selected_period_the_age_section_says_so_instead_of_guessing():
    rows = _section(_insights(fiscal_period=None), SECTION_AGE)
    assert list(rows.index) == ["Select a fiscal period to age the open items"] and rows["Rows"].sum() == 0


def test_concentration_ranks_customers_by_absolute_value_and_shares_sum_to_one():
    rows = _section(_insights(), SECTION_CUSTOMER)
    assert list(rows.index)[0] == "Acme"
    assert rows.loc["Acme", "Amount"] == 7011.40
    assert rows.loc["Bolt", "Amount"] == pytest.approx(73.45)                    # 123.45 less the 50.00 credit, net
    assert rows.loc["(no customer name)", "Rows"] == 1
    assert rows["Share of exceptions"].sum() == pytest.approx(1.0)
    assert (rows["Share of exceptions"] >= 0).all()


def test_only_the_top_customers_are_listed_and_the_rest_are_rolled_up():
    qb = _frame([(f"C{n:02d}", 100 * (n + 1), "P10-2026") for n in range(13)])
    rows = _section(build_open_item_insights(qb, INF, list(range(13)), [], fiscal_period=10, fiscal_year=2026,
                                             customer_col="Customer"), SECTION_CUSTOMER)
    assert len(rows) == 11 and rows.index[-1] == "All other customers (3)" and rows.iloc[-1]["Rows"] == 3


def test_no_customer_column_and_no_open_items_are_handled():
    rows = _section(_insights(customer_col=None), SECTION_CUSTOMER)
    assert list(rows.index) == ["No customer column is mapped"]
    assert build_open_item_insights(QB, INF, [], []).empty


def test_a_finished_run_carries_the_insights_and_they_change_no_result(mixed_result):  # noqa: F811
    insights = mixed_result.open_item_insights
    assert not insights.empty and list(insights.columns)[0] == "Section"
    true_unmatched = mixed_result.qb_dispositions["Final Disposition"].eq("TRUE_UNMATCHED").sum()
    same = _section(insights, SECTION_SAME_AMOUNT)
    quickbooks_rows = same.loc[same.index.str.startswith("Unmatched QuickBooks"), "Rows"].sum()
    assert quickbooks_rows <= len(mixed_result.unmatched_qb) and true_unmatched >= 1
    assert mixed_result.controls["Status"].eq("PASS").all()
