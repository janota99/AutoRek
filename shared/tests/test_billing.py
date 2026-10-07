from datetime import date

from shared.billing import card_brand, luhn_ok, parse_expiry, validate_card, validate_gift_card

TODAY = date(2026, 10, 5)


def test_luhn_and_brand():
    assert luhn_ok("4242424242424242") and card_brand("4242424242424242") == "Visa"
    assert luhn_ok("378282246310005") and card_brand("378282246310005") == "American Express"
    assert luhn_ok("5555555555554444") and card_brand("5555555555554444") == "Mastercard"
    assert not luhn_ok("4242424242424241")
    assert not luhn_ok("abcd")


def test_expiry():
    assert parse_expiry("09/27") == (9, 2027)
    assert parse_expiry("9 / 2027") == (9, 2027)
    assert parse_expiry("13/27") is None and parse_expiry("0927") is None


def test_valid_card_keeps_only_brand_and_last4():
    check = validate_card("Pat Lee", "4242 4242 4242 4242", "10/26", "123", today=TODAY)
    assert check.ok and (check.brand, check.last4) == ("Visa", "4242")


def test_card_problems():
    check = validate_card("", "4242424242424241", "09/26", "12", today=TODAY)
    assert len(check.problems) == 4 and not check.ok and check.last4 == ""
    assert validate_card("A", "378282246310005", "12/30", "123", today=TODAY).problems  # Amex needs 4 digits
    assert validate_card("A", "378282246310005", "12/30", "1234", today=TODAY).ok


def test_gift_card():
    assert validate_gift_card("ABCD-1234-EFGH") == ()
    assert validate_gift_card("123") != ()
