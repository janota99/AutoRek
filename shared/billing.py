"""Payment-form checks for the Plans page's demo checkout. No payment processor is connected.

Nothing here stores or sends a card: ``validate_card`` returns problems and, when the card passes, only the brand
and last four digits (what a charge register may show). A real checkout must use a processor's hosted card fields
so card numbers never reach this app at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CardCheck:
    problems: tuple[str, ...]
    brand: str = ""
    last4: str = ""

    @property
    def ok(self) -> bool:
        return not self.problems


def digits(text: str) -> str:
    return re.sub(r"[\s-]", "", text or "")


def luhn_ok(number: str) -> bool:
    """The check-digit test every real card number passes."""
    if not number.isdigit() or not 12 <= len(number) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def card_brand(number: str) -> str:
    if re.match(r"^3[47]", number):
        return "American Express"
    if number.startswith("4"):
        return "Visa"
    if re.match(r"^(5[1-5]|2(2[2-9]\d|[3-6]\d\d|7[01]\d|720))", number):
        return "Mastercard"
    if re.match(r"^(6011|65|64[4-9])", number):
        return "Discover"
    return "Card"


def parse_expiry(text: str) -> tuple[int, int] | None:
    """(month, four-digit year) from MM/YY or MM/YYYY, else None."""
    m = re.fullmatch(r"\s*(\d{1,2})\s*/\s*(\d{2}|\d{4})\s*", text or "")
    if not m:
        return None
    month, year = int(m.group(1)), int(m.group(2))
    if year < 100:
        year += 2000
    return (month, year) if 1 <= month <= 12 else None


def validate_card(name: str, number: str, expiry: str, cvc: str, *, today: date | None = None) -> CardCheck:
    today = today or date.today()
    problems = []
    num = digits(number)
    if not name.strip():
        problems.append("Enter the name on the card.")
    if not luhn_ok(num):
        problems.append("The card number is not valid.")
    brand = card_brand(num) if num else ""
    exp = parse_expiry(expiry)
    if exp is None:
        problems.append("Enter the expiration date as MM/YY.")
    elif (exp[1], exp[0]) < (today.year, today.month):
        problems.append("The card has expired.")
    want = 4 if brand == "American Express" else 3
    if not (cvc.isdigit() and len(cvc) == want):
        problems.append(f"The security code (CVC) should be {want} digits.")
    if problems:
        return CardCheck(tuple(problems))
    return CardCheck((), brand, num[-4:])


def validate_gift_card(code: str) -> tuple[str, ...]:
    cleaned = re.sub(r"[\s-]", "", code or "")
    if not re.fullmatch(r"[A-Za-z0-9]{8,24}", cleaned):
        return ("Enter the gift card code (8 to 24 letters and numbers).",)
    return ()
