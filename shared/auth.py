"""Real sign-in through Streamlit's OpenID Connect support (``st.login``), and who it lets count as verified.

Streamlit does the sign-in itself, from the ``[auth]`` block of ``.streamlit/secrets.toml`` (see ``secrets.toml.example``).
This module adds one rule on top: a person is **verified** only when the provider authenticated them, did not mark their
email unverified, and their email (or its domain) is on the allow-list in the ``[access]`` block. Without an allow-list
nobody is verified, because "can sign in to Google" says nothing about belonging to your organization.

Verification here only labels who did what in the audit trail (``shared/identity.py``). It does not yet restrict what a
person can open, and the Invoice Hub's browser-only sign-in remains unverified personalization.

Streamlit-free except for the two thin wrappers at the bottom, so the rules are tested with plain objects.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

REQUIRED_AUTH_KEYS = ("redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url")

SIGNED_OUT = "signed_out"
VERIFIED = "verified"
NOT_ALLOWED = "not_allowed"
NO_ALLOW_LIST = "no_allow_list"
EMAIL_UNVERIFIED = "email_unverified"


@dataclass(frozen=True)
class AccessRules:
    emails: frozenset[str] = frozenset()
    domains: frozenset[str] = frozenset()

    @property
    def configured(self) -> bool:
        return bool(self.emails or self.domains)

    def allows(self, email: str) -> bool:
        email = email.strip().lower()
        if "@" not in email:
            return False
        return email in self.emails or email.rsplit("@", 1)[1] in self.domains


@dataclass(frozen=True)
class AuthState:
    status: str
    email: str = ""
    name: str = ""

    @property
    def verified(self) -> bool:
        return self.status == VERIFIED

    @property
    def signed_in(self) -> bool:
        return self.status != SIGNED_OUT


def _clean(items: Any) -> frozenset[str]:
    if isinstance(items, str):
        items = [items]
    return frozenset(str(i).strip().lower().lstrip("@") for i in (items or []) if str(i).strip())


def load_access(secrets: Any) -> AccessRules:
    """The allow-list from the ``[access]`` block (``emails`` and/or ``domains``); empty when absent or unreadable."""
    try:
        section = secrets["access"] if secrets is not None and "access" in secrets else {}
        return AccessRules(_clean(section.get("emails")), _clean(section.get("domains")))
    except Exception:  # noqa: BLE001 - a missing or unreadable secrets file means "no allow-list"
        return AccessRules()


def missing_auth_settings(secrets: Any) -> Optional[list[str]]:
    """None when no ``[auth]`` block exists (sign-in is simply off); otherwise the required keys it lacks."""
    try:
        if secrets is None or "auth" not in secrets:
            return None
        section = secrets["auth"]
        return [key for key in REQUIRED_AUTH_KEYS if not section.get(key)]
    except Exception:  # noqa: BLE001
        return None


def evaluate(user: Any, rules: AccessRules) -> AuthState:
    """Where a ``st.user``-like object stands. Never raises: anything odd counts as signed out."""
    try:
        if not bool(getattr(user, "is_logged_in", False)):
            return AuthState(SIGNED_OUT)
        email = str(getattr(user, "email", "") or "").strip()
        name = str(getattr(user, "name", "") or "").strip()
        if not email and not name:
            return AuthState(SIGNED_OUT)
        if getattr(user, "email_verified", None) is False:
            return AuthState(EMAIL_UNVERIFIED, email, name)
        if not rules.configured:
            return AuthState(NO_ALLOW_LIST, email, name)
        if not rules.allows(email):
            return AuthState(NOT_ALLOWED, email, name)
        return AuthState(VERIFIED, email, name)
    except Exception:  # noqa: BLE001 - st.user raises when no authentication is configured
        return AuthState(SIGNED_OUT)


EXPLANATIONS = {
    NOT_ALLOWED: "You are signed in, but this email is not on the workspace's allow-list, so your actions are "
                 "recorded as unverified. Ask an administrator to add it.",
    NO_ALLOW_LIST: "You are signed in, but no allow-list is configured (the [access] block), so nobody can be "
                   "verified yet and your actions are recorded as unverified.",
    EMAIL_UNVERIFIED: "Your sign-in provider reports this email as unverified, so your actions are recorded as "
                      "unverified.",
}


def emails_text(items: Iterable[str]) -> str:
    return ", ".join(sorted(items))
