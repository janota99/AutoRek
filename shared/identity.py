"""Who is using the app, for the audit trail.

A name counts as **verified** only when Streamlit's real sign-in (``st.login``, OpenID Connect) has authenticated the
person. The Invoice Hub's browser-only sign-in lives in sessionStorage, which Python cannot read and which anyone can
edit, so it never produces a verified identity. With no sign-in configured every action is recorded as an unsigned
user, and the history says so rather than guessing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Identity:
    name: str
    verified: bool

    @property
    def label(self) -> str:
        return self.name if self.verified else f"{self.name} (unverified)"


UNSIGNED_NAME = "Unsigned user"


def identity_from_user(user: Any) -> Identity:
    """The identity for a ``st.user``-like object. Never raises: a missing or odd object is an unsigned user."""
    try:
        if not bool(getattr(user, "is_logged_in", False)):
            return Identity(UNSIGNED_NAME, False)
        email = str(getattr(user, "email", "") or "").strip()
        name = str(getattr(user, "name", "") or "").strip()
        shown = email or name
        if not shown:
            return Identity(UNSIGNED_NAME, False)
        return Identity(shown, True)
    except Exception:  # noqa: BLE001 - st.user raises when no authentication is configured
        return Identity(UNSIGNED_NAME, False)


def current_identity() -> Identity:
    import streamlit as st

    return identity_from_user(st.user)
