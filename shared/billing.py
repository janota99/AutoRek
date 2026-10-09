"""Payment-form checks for the Plans page's demo checkout. No payment processor is connected.

Nothing here stores or sends a card: ``validate_card`` returns problems and, when the card passes, only the brand
and last four digits (what a charge register may show). A real checkout must use a processor's hosted card fields
so card numbers never reach this app at all.

The demo account checks live here too, and are equally simulated: passwords are kept only as a salted
PBKDF2 hash for the browser session, and the second factor is an offline authenticator-app code (TOTP, RFC 6238),
which needs no phone number, email or network. None of it controls access to any tool.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import struct
import time
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote


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


# --- billing address ---------------------------------------------------------------------------------------------------

def validate_address(street: str, city: str, state: str, postal: str, country: str) -> tuple[str, ...]:
    """Every billing-address field is required; a US ZIP must be 5 digits or ZIP+4."""
    problems = []
    if not street.strip():
        problems.append("Enter the billing street address.")
    if not city.strip():
        problems.append("Enter the billing city.")
    if not state.strip():
        problems.append("Enter the billing state or region.")
    if not postal.strip():
        problems.append("Enter the billing ZIP or postal code.")
    elif country.strip().lower() in {"united states", "us", "usa"} and not re.fullmatch(r"\d{5}(-\d{4})?", postal.strip()):
        problems.append("A US ZIP code is 5 digits (or ZIP+4).")
    if not country.strip():
        problems.append("Enter the billing country.")
    return tuple(problems)


# --- demo login --------------------------------------------------------------------------------------------------------

_USERNAME_RE = re.compile(r"[A-Za-z0-9._-]{4,32}")
_PBKDF2_ROUNDS = 200_000


def validate_login(username: str, password: str, confirm: str) -> tuple[str, ...]:
    problems = []
    if not _USERNAME_RE.fullmatch(username.strip()):
        problems.append("The login name is 4 to 32 letters, numbers, dots, dashes or underscores.")
    if len(password) < 12:
        problems.append("The password must be at least 12 characters.")
    elif not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        problems.append("The password must contain letters and numbers.")
    if password != confirm:
        problems.append("The two passwords do not match.")
    return tuple(problems)


def hash_password(password: str) -> tuple[str, str]:
    """(salt, hash), both hex. The password itself is never kept."""
    salt = secrets.token_bytes(16)
    return salt.hex(), hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS).hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), _PBKDF2_ROUNDS).hex()
    return hmac.compare_digest(check, hash_hex)


# --- offline two-factor authentication (TOTP) -------------------------------------------------------------------------

TOTP_STEP = 30
TOTP_DIGITS = 6


def new_totp_secret() -> str:
    """A fresh base32 secret (160 bits) to type into an authenticator app."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_secret_display(secret: str) -> str:
    """The secret in groups of four, easier to type into an authenticator app."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def totp_uri(secret: str, account: str, issuer: str = "Janota FIN") -> str:
    """The otpauth address an authenticator app imports; shown as text because no QR library is installed."""
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits={TOTP_DIGITS}&period={TOTP_STEP}"


def _code_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    number = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % 10 ** TOTP_DIGITS
    return f"{number:0{TOTP_DIGITS}d}"


def totp_now(secret: str, *, at: float | None = None) -> str:
    return _code_at(secret, int((time.time() if at is None else at) // TOTP_STEP))


def verify_totp(secret: str, code: str, *, at: float | None = None, last_used: int = -1) -> int | None:
    """The time-step counter the code matches (one step either side allowed for clock drift), else None.
    A counter at or before ``last_used`` is refused, so a code works once."""
    code = re.sub(r"\s", "", code or "")
    if not re.fullmatch(rf"\d{{{TOTP_DIGITS}}}", code):
        return None
    now = int((time.time() if at is None else at) // TOTP_STEP)
    for counter in (now - 1, now, now + 1):
        if counter > last_used and hmac.compare_digest(_code_at(secret, counter), code):
            return counter
    return None
