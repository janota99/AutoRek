"""Plain-language error panel shared by the apps that use Streamlit's own widgets.

(Recon has its own styled version, `render_friendly_error` in apps/recon/ui_components.py.)
Display only: callers keep their own `return`, so a failure still stops the run exactly as before.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st


def friendly_error(title: str, what_to_do: str, exc: Optional[BaseException] = None) -> None:
    """Show what went wrong and the next step; the raw error stays one click away."""
    st.error(f"**{title}**\n\n{what_to_do}")
    if exc is not None:
        with st.expander("Technical details (for support)"):
            st.code(f"{type(exc).__name__}: {exc}")
