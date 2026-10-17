"""Run the live database tests against a throwaway embedded PostgreSQL, so no server needs to be installed.

    pip install pixeltable-pgserver          # once; a pip-installable PostgreSQL (pgserver itself has no Windows/3.13 build)
    py -m db.run_live_tests                  # runs `pytest db`; extra arguments go to pytest, e.g. -k runs -x

It starts a temporary server in a scratch folder, points DB_TEST_ADMIN_URL at it, runs the tests (each creates and drops
its own database), and stops and deletes the server. It never touches the database in .streamlit/secrets.toml.

The embedded server ships without the `pgcrypto` extension, which migration 001 requests. PostgreSQL 13+ has
gen_random_uuid() built in, so for this run only that one line is removed from the migration text in memory; the file
on disk, and what a real server or Supabase receives, is unchanged. Run the tests once against your real server setup
(DB_TEST_ADMIN_URL=<admin url> py -m pytest db) before relying on a migration.
"""
from __future__ import annotations

import dataclasses
import os
import shutil
import sys
import tempfile

PGCRYPTO_LINE = "CREATE EXTENSION IF NOT EXISTS pgcrypto;"


def _skip_pgcrypto() -> None:
    from . import migrate

    original = migrate.discover

    def discover(*args, **kwargs):
        return [dataclasses.replace(m, sql=m.sql.replace(PGCRYPTO_LINE, "")) for m in original(*args, **kwargs)]

    migrate.discover = discover  # before the tests' conftest imports it


def main(argv: list[str]) -> int:
    try:
        import pixeltable_pgserver as pgserver
    except ImportError:
        print("Install the embedded server first:  pip install pixeltable-pgserver", file=sys.stderr)
        return 2
    import pytest

    folder = tempfile.mkdtemp(prefix="autorek_pg_")
    server = pgserver.get_server(os.path.join(folder, "data"), cleanup_mode="stop")
    try:
        os.environ["DB_TEST_ADMIN_URL"] = server.get_uri()
        _skip_pgcrypto()
        return int(pytest.main(["db", "-q", "-p", "no:cacheprovider", *argv]))
    finally:
        server.cleanup()
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
