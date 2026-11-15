"""Workspace settings in PostgreSQL (table from db/migrations/005), one JSON document per organization and name.

Same contract as the other stores: every function takes a connection from ``db.connection.tenant_connection``, which has
already scoped the transaction to one organization, and does no commit of its own.
"""
from __future__ import annotations

from typing import Any, Optional

FISCAL = "fiscal"


def get(conn, name: str) -> Optional[dict]:
    """The saved document, or None when nothing has been saved under that name."""
    row = conn.execute("SELECT value FROM workspace_settings WHERE name = %s", (name,)).fetchone()
    return None if row is None else dict(row[0])


def put(conn, org_id: str, name: str, value: dict, *, updated_by: str = "app") -> None:
    """Create or replace the document (the whole document, as a file save would)."""
    from psycopg.types.json import Jsonb

    conn.execute(
        "INSERT INTO workspace_settings (org_id, name, value, updated_by) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (org_id, name) DO UPDATE SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by",
        (org_id, name, Jsonb(value), updated_by))
