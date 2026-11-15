"""The shared Trial Balance cache in PostgreSQL (table from db/migrations/005).

Same contract as the other stores: every function takes a connection from ``db.connection.tenant_connection``, which has
already scoped the transaction to one organization, and does no commit of its own.

The table is append-only. The newest row is the current trial balance and the row before it is the one-version backup, so
"replace", "restore the backup" and "clear" are each one inserted row and nothing is edited or lost:

    upload / restore   a workbook row; the previous current becomes the backup
    clear_all          hides every earlier row (the current file and its backup)
    clear_current      hides the current file only; the backup stays available
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

_NEWEST = ("SELECT version_id, kind, created_at, sha256, content IS NOT NULL FROM sales_tax_trial_balance_versions "
           "ORDER BY version_id DESC LIMIT 3")


@dataclass(frozen=True)
class Version:
    version_id: int
    kind: str
    created_at: datetime
    sha256: Optional[str]
    has_file: bool


def _newest(conn) -> list[Version]:
    return [Version(*row) for row in conn.execute(_NEWEST).fetchall()]


def current_version(conn) -> Optional[Version]:
    """The version that is the current trial balance, or None when there is none (never saved, or cleared)."""
    rows = _newest(conn)
    return rows[0] if rows and rows[0].has_file else None


def backup_version(conn) -> Optional[Version]:
    """The version that is the one-version backup, or None."""
    rows = _newest(conn)
    if not rows or rows[0].kind == "clear_all":
        return None
    # Newest row is the current file: the one before it is the backup. Newest row is a clear_current marker: the file the
    # marker hid was the current one, so the backup is the file before that. A clear_all marker hid everything, handled above.
    behind = 2 if rows[0].kind == "clear_current" else 1
    return rows[behind] if len(rows) > behind and rows[behind].has_file else None


def read_file(conn, version_id: int) -> bytes:
    return bytes(conn.execute("SELECT content FROM sales_tax_trial_balance_versions WHERE version_id = %s",
                              (version_id,)).fetchone()[0])


def save(conn, org_id: str, data: bytes, *, kind: str = "upload", saved_by: str = "app") -> tuple[int, datetime]:
    """Append a workbook (kind 'upload' or 'restore'); returns (version id, when it was saved)."""
    if kind not in ("upload", "restore"):
        raise ValueError(kind)
    return conn.execute(
        "INSERT INTO sales_tax_trial_balance_versions (org_id, kind, content, sha256, size_bytes, saved_by) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING version_id, created_at",
        (org_id, kind, data, hashlib.sha256(data).hexdigest(), len(data), saved_by)).fetchone()


def clear(conn, org_id: str, *, include_backup: bool = True, saved_by: str = "app") -> int:
    """Hide the current trial balance (and, by default, its backup). Returns how many files that hid."""
    hidden = int(current_version(conn) is not None) + (int(backup_version(conn) is not None) if include_backup else 0)
    if hidden:
        conn.execute("INSERT INTO sales_tax_trial_balance_versions (org_id, kind, saved_by) VALUES (%s, %s, %s)",
                     (org_id, "clear_all" if include_backup else "clear_current", saved_by))
    return hidden
