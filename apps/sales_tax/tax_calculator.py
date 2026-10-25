"""Texas sales tax calculator logic, no Streamlit.

A forward calculation only: given a pre-tax price, a location, and whether the sale is SaaS /
data processing, split the tax between the State of Texas and the local jurisdictions. It reads no
file and touches no ledger; nothing here feeds Transaction Cleanup or the workbook.

Rules the screen relies on:
- The State rate is 6.25%. Local taxes (city + county + special districts) are capped at 2.00%, so
  the most any Texas location can charge is 8.25% (``DEFAULT_COMBINED_RATE``).
- SaaS is treated as data processing: only 80% of the price is taxable (``SAAS_TAXABLE_SHARE``,
  34 TAC §3.330). The State and local rates both apply to that 80%, never to the full price.
- Each tax line is rounded half-up to the cent on its own and the total is the sum of the rounded
  lines, so the breakdown always adds up exactly. Money is ``Decimal``, never float.
- The two built-in locations (Standard Texas 8.25% and AFI at 6.75%) are permanent; any other business
  outside city limits should enter its own county and district rates as a custom location.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
STATE_RATE = Decimal("6.25")           # percent
LOCAL_CAP = Decimal("2.00")            # percent: city + county + special districts
DEFAULT_COMBINED_RATE = STATE_RATE + LOCAL_CAP   # 8.25
SAAS_TAXABLE_SHARE = Decimal("0.80")


@dataclass(frozen=True)
class Location:
    """A taxing location. Rates are percents of the taxable amount (``Decimal("0.50")`` is 0.50%)."""
    name: str
    city_rate: Decimal = Decimal("0")
    county_rate: Decimal = Decimal("0")
    district_rate: Decimal = Decimal("0")
    combined_local_rate: Decimal = Decimal("0")   # a single undivided local rate, e.g. the 2.00% default
    note: str = ""
    county_name: str = "County"                   # how the county line is labelled, e.g. "Randall County"

    @property
    def local_rate(self) -> Decimal:
        return self.city_rate + self.county_rate + self.district_rate + self.combined_local_rate

    @property
    def combined_rate(self) -> Decimal:
        return STATE_RATE + self.local_rate

    @property
    def over_cap(self) -> bool:
        return self.local_rate > LOCAL_CAP


# Permanent locations. "Standard" is the full 8.25% default. AFI (the company this tool is built for) is outside
# Amarillo city limits, so it collects State 6.25% plus Randall County 0.50% = 6.75% on a standard sale; the SaaS 80%
# taxable base still applies to it like any other location.
STANDARD = Location("Standard Texas (8.25%)", combined_local_rate=Decimal("2.00"),
                    note="Default: the maximum 2.00% local rate on top of the 6.25% State rate.")
AFI = Location("AFI (outside Amarillo city limits)", county_rate=Decimal("0.50"), county_name="Randall County",
               note="AFI: outside Amarillo city limits, so State 6.25% + Randall County 0.50% = 6.75% on a standard "
                    "sale. SaaS is still taxed on 80% of the price.")
PRESET_LOCATIONS: tuple[Location, ...] = (STANDARD, AFI)


@dataclass(frozen=True)
class TaxLine:
    label: str
    rate: Decimal            # percent of the taxable amount
    effective_rate: Decimal  # percent of the price
    amount: Decimal


@dataclass(frozen=True)
class TaxResult:
    price: Decimal
    saas: bool
    location: Location
    taxable_amount: Decimal
    state: TaxLine
    local_lines: tuple[TaxLine, ...]

    @property
    def local_total(self) -> Decimal:
        return sum((ln.amount for ln in self.local_lines), Decimal("0"))

    @property
    def total_tax(self) -> Decimal:
        return self.state.amount + self.local_total

    @property
    def total_due(self) -> Decimal:
        return self.price + self.total_tax

    @property
    def effective_rate(self) -> Decimal:
        """Total tax as a percent of the price (5.400% for $500 SaaS at 6.25% + 0.50%)."""
        return (self.total_tax / self.price * 100) if self.price else Decimal("0")


def to_cents(value) -> Decimal:
    """Round any number-like value to whole cents, half-up."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_price(text) -> tuple[Decimal, str | None]:
    """The price typed in the live field: (dollars, a problem message or None).

    Blank is $0.00 with no message. Anything that is not a non-negative number is also $0.00, with a message, so the
    screen never shows a result for text it could not read. ``$`` and thousands commas are ignored."""
    cleaned = str(text or "").replace("$", "").replace(",", "").strip()
    if not cleaned:
        return Decimal("0.00"), None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0.00"), f'"{text}" is not a number, so the price is treated as $0.00.'
    if not value.is_finite() or value < 0:
        return Decimal("0.00"), "The price can't be negative or infinite, so it is treated as $0.00."
    return to_cents(value), None


def _line(label: str, rate: Decimal, taxable: Decimal, share: Decimal) -> TaxLine:
    return TaxLine(label, rate, rate * share, to_cents(taxable * rate / 100))


def calculate(price, location: Location, saas: bool) -> TaxResult:
    """Tax on ``price`` (pre-tax, in dollars) at ``location``; ``saas`` limits the base to 80%."""
    price = to_cents(price)
    if price < 0:
        raise ValueError("Price cannot be negative.")
    share = SAAS_TAXABLE_SHARE if saas else Decimal("1")
    taxable = to_cents(price * share)
    local = tuple(
        _line(label, rate, taxable, share)
        for label, rate in (("Local (city, county, districts)", location.combined_local_rate),
                            ("City", location.city_rate), (location.county_name, location.county_rate),
                            ("Special district", location.district_rate))
        if rate > 0
    )
    return TaxResult(price, saas, location, taxable, _line("Texas State", STATE_RATE, taxable, share), local)


def compare(price, locations: list[Location] | tuple[Location, ...]) -> list[tuple[Location, TaxResult, TaxResult]]:
    """For each location: (location, non-SaaS result, SaaS result), for the side-by-side table."""
    return [(loc, calculate(price, loc, False), calculate(price, loc, True)) for loc in locations]
