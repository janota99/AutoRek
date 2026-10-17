"""The sign-in controls: a compact status line with Sign in / Sign out, drawn only when ``[auth]`` is configured.

With no ``[auth]`` block it draws nothing, so the app looks and works as before. With a block that is missing keys it
says which ones, rather than offering a button that would fail.
"""
from __future__ import annotations

import streamlit as st

from .auth import EXPLANATIONS, evaluate, load_access, missing_auth_settings


def render_sign_in() -> None:
    try:
        secrets = st.secrets
        missing = missing_auth_settings(secrets)
    except Exception:  # noqa: BLE001
        return
    if missing is None:
        return
    if missing:
        st.caption(f"Sign-in is not finished: the [auth] block in secrets.toml is missing {', '.join(missing)}.")
        return
    state = evaluate(st.user, load_access(secrets))
    who, action = st.columns([4, 1], vertical_alignment="center")
    with who:
        if not state.signed_in:
            st.caption("Sign in so the run history records who did what, verified. Do it before uploading files: signing in reloads the page.")
        elif state.verified:
            st.caption(f"Signed in as {state.email or state.name} (verified).")
        else:
            st.caption(f"Signed in as {state.email or state.name} (unverified). {EXPLANATIONS[state.status]}")
    with action:
        if state.signed_in:
            st.button("Sign out", key="auth_sign_out", on_click=st.logout, width="stretch")
        else:
            st.button("Sign in", key="auth_sign_in", on_click=st.login, type="primary", width="stretch")
