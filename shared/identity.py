"""Who is using the app, for the audit trail.

A name counts as **verified** only when Streamlit's real sign-in (``st.login``, OpenID Connect) authenticated the person
and ``shared/auth.py``'s rules accept them (email not marked unverified, and on the ``[access]`` allow-list). The Invoice
Hub's browser-only sign-in lives in sessionStorage, which Python cannot read and anyone can edit, so it never produces a
verified identity. With no sign-in configured every action is recorded as an unsigned user, and the history says so
rather than guessing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .auth import AccessRules, evaluate


@dataclass(frozen=True)
class Identity:
    name: str
    verified: bool

    @property
    def label(self) -> str:
        return self.name if self.verified else f"{self.name} (unverified)"


UNSIGNED_NAME = "Unsigned user"


def identity_from_user(user: Any, rules: Optional[AccessRules] = None) -> Identity:
    """The identity for a ``st.user``-like object. Never raises: a missing or odd object is an unsigned user.

    A signed-in person who is not verified (no allow-list, not on it, or an unverified email) keeps their name, marked
    unverified, so the log still says who it probably was without vouching for it."""
    state = evaluate(user, rules or AccessRules())
    if not state.signed_in:
        return Identity(UNSIGNED_NAME, False)
    return Identity(state.email or state.name, state.verified)


def current_identity() -> Identity:
    import streamlit as st

    from .auth import load_access

    try:
        rules = load_access(st.secrets)
    except Exception:  # noqa: BLE001
        rules = AccessRules()
    return identity_from_user(st.user, rules)
