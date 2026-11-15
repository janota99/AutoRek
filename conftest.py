"""Test-wide setup.

No test may reach a real database through the developer's own ``.streamlit/secrets.toml``: ``shared.database.config`` (what the
pages use to find the database) answers "none configured" unless a test sets up its own. Tests that exercise PostgreSQL
pass an explicit config (see db/tests).
"""
import pytest


@pytest.fixture(autouse=True)
def _no_database_from_local_secrets(monkeypatch):
    from shared import database

    monkeypatch.setattr(database, "config", lambda: None)
