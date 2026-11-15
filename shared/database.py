"""The Streamlit side of "is a database configured?", shared by every page that can save to PostgreSQL.

``config()`` reads the ``[database]`` secrets block (or the AUTOREK_* environment variables) through db.connection. It returns
None when no database is set up, so every caller keeps working from local files; a half-filled or invalid setting raises
``DatabaseConfigError``, which callers report instead of quietly using a different store.
"""
from __future__ import annotations

from typing import Optional

import streamlit as st

from db.connection import DatabaseConfig, load_config


def config() -> Optional[DatabaseConfig]:
    return load_config(secrets=st.secrets)
