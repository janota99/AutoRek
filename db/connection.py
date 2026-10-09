"""How the app reaches PostgreSQL: configuration, and a connection scoped to one organization.

The app connects as `app_login` (migration 003), never as the owner. Row-level security shows it only the rows of the
organization named in `app.org_id`, which this module sets at the start of every transaction. Before doing anything
it refuses a connection that is a superuser, can bypass row-level security, or is not an `app_runtime_role` member,
so a mistaken owner connection string fails loudly instead of silently ignoring tenant isolation.

Settings come from environment variables (which win, so a test can point at a local database) or Streamlit secrets. The variable names are deliberately NOT
`DATABASE_URL` (the owner string `db.migrate` uses), so the app can never pick up owner credentials by accident:

    # .streamlit/secrets.toml (never committed)          environment equivalent
    [database]                                           AUTOREK_APP_DATABASE_URL
    url    = "postgresql://app_login.<ref>:<pw>@..."     AUTOREK_ORG_ID
    org_id = "<uuid>"
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional

URL_ENV = "AUTOREK_APP_DATABASE_URL"
ORG_ENV = "AUTOREK_ORG_ID"


class DatabaseConfigError(Exception):
    """The settings are present but unusable, or the connection is not the restricted app role."""


@dataclass(frozen=True)
class DatabaseConfig:
    url: str
    org_id: str

    def __repr__(self) -> str:  # never print the connection string
        return f"DatabaseConfig(org_id={self.org_id!r}, url=<hidden>)"


def load_config(environ: Optional[Mapping[str, str]] = None, secrets: Any = None) -> Optional[DatabaseConfig]:
    """The configured database, or None when none is set up (the app then works without one)."""
    env = os.environ if environ is None else environ
    section: Mapping[str, Any] = {}
    try:
        if secrets is not None and "database" in secrets:
            section = secrets["database"]
    except Exception:  # noqa: BLE001 - a missing or unreadable secrets file means "not configured"
        section = {}
    if env.get(URL_ENV) or env.get(ORG_ENV):  # an explicit environment setting wins over the secrets file, as a whole
        url, org = env.get(URL_ENV), env.get(ORG_ENV)
    else:
        url, org = section.get("url"), section.get("org_id")
    if not url and not org:
        return None
    if not url or not org:
        raise DatabaseConfigError("Both a database url and an org_id are needed.")
    try:
        org = str(uuid.UUID(str(org)))
    except ValueError as exc:
        raise DatabaseConfigError("org_id is not a valid UUID.") from exc
    return DatabaseConfig(url=str(url), org_id=org)


def _assert_app_role(conn) -> None:
    row = conn.execute(
        "SELECT r.rolsuper OR r.rolbypassrls, pg_has_role(current_user, 'app_runtime_role', 'MEMBER') "
        "FROM pg_roles r WHERE r.rolname = current_user").fetchone()
    if row is None or row[0] or not row[1]:
        raise DatabaseConfigError(
            "The app must connect as the restricted app_login role, not as an owner or administrator.")


@contextmanager
def tenant_connection(config: DatabaseConfig) -> Iterator[Any]:
    """One transaction scoped to the configured organization; commits on success, rolls back on any error."""
    import psycopg  # imported here so the rest of the app runs without the driver installed

    conn = psycopg.connect(config.url, connect_timeout=8)
    try:
        with conn.transaction():
            _assert_app_role(conn)
            conn.execute("SELECT set_config('app.org_id', %s, true)", (config.org_id,))
            yield conn
    finally:
        conn.close()
