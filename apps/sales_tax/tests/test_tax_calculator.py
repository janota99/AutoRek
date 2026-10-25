"""Sales Tax Calculator: the Texas split, SaaS 80% base, and cent rounding."""
from decimal import Decimal

import pytest

from apps.sales_tax.tax_calculator import AFI, STANDARD, Location, calculate, compare

D = Decimal


def test_standard_sale_is_8_25_percent():
    r = calculate("500", STANDARD, saas=False)
    assert (r.state.amount, r.local_total, r.total_tax, r.total_due) == (D("31.25"), D("10.00"), D("41.25"), D("541.25"))


def test_saas_taxes_only_80_percent_of_price():
    r = calculate("500", STANDARD, saas=True)
    assert r.taxable_amount == D("400.00")
    assert (r.state.amount, r.local_total, r.total_due) == (D("25.00"), D("8.00"), D("533.00"))


def test_afi_saas_matches_reference_card():
    r = calculate("500", AFI, saas=True)
    assert (r.state.amount, r.local_total, r.total_tax, r.total_due) == (D("25.00"), D("2.00"), D("27.00"), D("527.00"))
    assert r.state.effective_rate == D("5.0000") and r.effective_rate == D("5.4")


def test_lines_are_rounded_each_and_sum_to_total():
    loc = Location("Odd", city_rate=D("1.25"), county_rate=D("0.50"), district_rate=D("0.25"))
    r = calculate("19.99", loc, saas=True)
    assert r.total_tax == r.state.amount + sum(ln.amount for ln in r.local_lines)
    assert r.total_due == D("19.99") + r.total_tax


def test_zero_price_and_negative_price():
    assert calculate("0", STANDARD, False).total_due == D("0.00")
    with pytest.raises(ValueError):
        calculate("-1", STANDARD, False)


def test_compare_covers_every_location_both_ways():
    rows = compare("100", [STANDARD, AFI])
    assert [r[0] for r in rows] == [STANDARD, AFI]
    assert rows[1][1].total_due > rows[1][2].total_due   # SaaS is always cheaper than a standard sale


def test_tape_renders_png_and_pdf():
    from apps.sales_tax.tax_tape import tape_pdf, tape_png
    r = calculate("500", AFI, saas=True)
    assert tape_png(r, "Jan 01, 2026 09:00 AM")[:8] == b"\x89PNG\r\n\x1a\n"
    assert tape_pdf(r, "Jan 01, 2026 09:00 AM")[:5] == b"%PDF-"


def test_afi_is_6_75_percent_with_randall_county_and_keeps_the_saas_rule():
    from apps.sales_tax.tax_calculator import PRESET_LOCATIONS
    assert PRESET_LOCATIONS[:2] == (STANDARD, AFI)
    assert AFI.combined_rate == D("6.75") and AFI.county_rate == D("0.50") and AFI.city_rate == 0
    plain = calculate("1000", AFI, saas=False)
    assert (plain.state.amount, plain.local_total, plain.total_due) == (D("62.50"), D("5.00"), D("1067.50"))
    assert [ln.label for ln in plain.local_lines] == ["Randall County"]
    saas = calculate("1000", AFI, saas=True)
    assert saas.taxable_amount == D("800.00")
    assert (saas.state.amount, saas.local_total, saas.total_due) == (D("50.00"), D("4.00"), D("1054.00"))


def test_parse_price_blank_is_zero_and_bad_text_is_zero_with_a_message():
    from apps.sales_tax.tax_calculator import parse_price
    assert parse_price("") == (D("0.00"), None) and parse_price(None) == (D("0.00"), None)
    assert parse_price(" $1,234.5 ") == (D("1234.50"), None)
    assert parse_price("19.999") == (D("20.00"), None)
    for bad in ("abc", "1.2.3", "-5", "inf", "nan"):
        value, message = parse_price(bad)
        assert value == D("0.00") and message
