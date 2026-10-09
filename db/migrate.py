"""Apply the numbered SQL files in db/migrations to a PostgreSQL database, once each.

    py -m db.migrate              # applies anything new, using the DATABASE_URL environment variable
    py -m db.migrate --status     # lists applied and pending files without changing anything

A migration runs in its own transaction and is recorded in `schema_migrations` with a SHA-256 of its text. A file that
was already applied and has since been edited is refused: add a new numbered file instead of changing an old one.
The Streamlit app does not call this module and does not need a database yet. Install the driver first:
`pip install "psycopg[binary]"`. Never commit a connection string; keep it in the environment or `.streamlit/secrets.toml`.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_NAME = re.compile(r"^(\d{3,})_([a-z0-9_]+)\.sql$")

TRACKING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text PRIMARY KEY,
    name        text NOT NULL,
    sha256      text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str
    sha256: str


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Every migration file, in version order. Raises if a file name is malformed or a version repeats."""
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _NAME.match(path.name)
        if not match:
            raise MigrationError(f"'{path.name}' must be named like 001_short_description.sql")
        text = path.read_text(encoding="utf-8")
        found.append(Migration(match.group(1), match.group(2), text, hashlib.sha256(text.encode("utf-8")).hexdigest()))
    versions = [m.version for m in found]
    if len(versions) != len(set(versions)):
        raise MigrationError("Two migration files share a version number.")
    return found


def pending(migrations: Iterable[Migration], applied: dict[str, str]) -> list[Migration]:
    """Migrations not yet applied. Raises when an applied one was edited afterwards."""
    todo = []
    for migration in migrations:
        recorded = applied.get(migration.version)
        if recorded is None:
            todo.append(migration)
        elif recorded != migration.sha256:
            raise MigrationError(
                f"Migration {migration.version} ({migration.name}) was already applied and has been edited since. "
                "Put the change in a new numbered file instead."
            )
    return todo


def _connect(url: str):
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - needs the optional driver
        raise MigrationError('Install the driver first: pip install "psycopg[binary]"') from exc
    return psycopg.connect(url)


def apply_pending(conn, migrations: list[Migration]) -> list[Migration]:  # pragma: no cover - needs a live database
    """Apply every migration the database has not seen, each in its own transaction; return what was applied."""
    conn.execute(TRACKING_TABLE_SQL)
    applied = dict(conn.execute("SELECT version, sha256 FROM schema_migrations").fetchall())
    todo = pending(migrations, applied)
    for m in todo:
        with conn.transaction():
            conn.execute(m.sql)
            conn.execute("INSERT INTO schema_migrations (version, name, sha256) VALUES (%s, %s, %s)",
                         (m.version, m.name, m.sha256))
    return todo


def main(argv: list[str]) -> int:  # pragma: no cover - needs a live database
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL to a PostgreSQL connection string first.", file=sys.stderr)
        return 2
    migrations = discover()
    with _connect(url) as conn:
        if "--status" in argv:
            conn.execute(TRACKING_TABLE_SQL)
            applied = dict(conn.execute("SELECT version, sha256 FROM schema_migrations").fetchall())
            todo = pending(migrations, applied)
            for m in migrations:
                print(f"{m.version} {m.name}: {'pending' if m in todo else 'applied'}")
            return 0
        done = apply_pending(conn, migrations)
        for m in done:
            print(f"Applied {m.version} {m.name}")
        if not done:
            print("Nothing to apply.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
