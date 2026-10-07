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


# --- address, login, and the offline second factor --------------------------------------------------------------------

import base64


def test_address_needs_every_field_and_a_real_us_zip():
    from shared.billing import validate_address
    assert validate_address("1 Main St", "Chicago", "IL", "60601", "United States") == ()
    assert len(validate_address("", "", "", "", "")) == 5
    assert validate_address("1 Main St", "Chicago", "IL", "6060", "United States")      # not 5 digits
    assert validate_address("1 Main St", "London", "", "SW1A 1AA", "United Kingdom")    # only the US ZIP shape is checked


def test_login_rules_and_password_hash():
    from shared.billing import hash_password, validate_login, verify_password
    assert validate_login("jan.ota", "correct-horse-42", "correct-horse-42") == ()
    assert validate_login("ab", "short", "other")                                        # name, length and mismatch
    assert validate_login("jan.ota", "onlyletterslong", "onlyletterslong")               # needs a digit
    salt, digest = hash_password("correct-horse-42")
    assert "correct-horse-42" not in digest and verify_password("correct-horse-42", salt, digest)
    assert not verify_password("correct-horse-43", salt, digest)
    assert hash_password("correct-horse-42")[0] != salt                                  # a fresh salt each time


def test_totp_matches_the_rfc_6238_vector_and_works_once():
    from shared.billing import _code_at, verify_totp
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert _code_at(secret, 59 // 30) == "287082"                                        # RFC 6238, SHA-1, t = 59
    step = verify_totp(secret, "287082", at=59)
    assert step == 1
    assert verify_totp(secret, "287082", at=59, last_used=step) is None                  # no reuse
    assert verify_totp(secret, "287 082", at=65) == 1                                    # spaces and one step of drift are fine
    assert verify_totp(secret, "000000", at=59) is None and verify_totp(secret, "12345", at=59) is None
