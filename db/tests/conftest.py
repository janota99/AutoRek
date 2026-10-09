"""Shared fixtures for the live database tests: a throwaway database with every migration applied.

Skipped unless DB_TEST_ADMIN_URL points at a server where the tests may create (and drop) their own database, for example
postgresql://postgres@127.0.0.1:55432/postgres . Needs `pip install "psycopg[binary]"`.
"""

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import make_conninfo  # noqa: E402

from db.migrate import apply_pending, discover  # noqa: E402

ADMIN_URL = os.environ.get("DB_TEST_ADMIN_URL")


@pytest.fixture(scope="session")
def db_url():
    name = f"autorek_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = make_conninfo(ADMIN_URL, dbname=name)
    with psycopg.connect(url) as conn:
        # Imitate Supabase: its web roles exist and receive every new public table by default.
        for role in ("anon", "authenticated"):
            conn.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
                         f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$")
        for kind in ("TABLES", "SEQUENCES"):
            conn.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON {kind} TO anon, authenticated")
        conn.commit()
        applied = apply_pending(conn, discover())
        assert [m.version for m in applied] == [m.version for m in discover()]
        assert apply_pending(conn, discover()) == []  # a second run does nothing
    yield url
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def conn(db_url):
    with psycopg.connect(db_url) as connection:
        yield connection


@pytest.fixture
def org(conn):
    """A fresh organization with two products."""
    org_id = uuid.uuid4()
    with conn.transaction():
        conn.execute("INSERT INTO organizations (org_id, name) VALUES (%s, 'Test Co')", (org_id,))
        conn.execute("INSERT INTO fifo_products (org_id, alias, name) VALUES (%s, 1, 'Caps'), (%s, 2, 'Labels')",
                     (org_id, org_id))
    return org_id




def pytest_collection_modifyitems(config, items):
    if ADMIN_URL:
        return
    skip = pytest.mark.skip(reason="set DB_TEST_ADMIN_URL to run the live database tests")
    for item in items:
        if "db_url" in item.fixturenames or "conn" in item.fixturenames:
            item.add_marker(skip)
