"""Is MongoDB really ready to connect? One command answers it.

    py -m mongodb.check_connection

Reads the same settings the app will (``AUTOREK_MONGODB_URI`` or the ``[mongodb]`` block in
``.streamlit/secrets.toml``), then checks, in order:

  1. a connection string is configured and well formed
  2. the cluster answers a ping (catches wrong password, IP not allowed, paused cluster)
  3. the database holds the three collections, with the expected document counts
  4. every stored document still satisfies its local schema (``mongodb/schemas``)
  5. the database user is not over-privileged (a warning, not a failure)

It only reads; it never writes, creates, or drops anything. The exit status is 0 when every check passes.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from .check_samples import COLLECTIONS, ObjectId, load_documents, load_schema, validate
from .connection import MongoConfigError, describe_error, load_config

ROOT = Path(__file__).resolve().parent.parent
SECRETS_PATH = ROOT / ".streamlit" / "secrets.toml"
_ADMIN_ROLES = {"atlasAdmin", "root", "dbAdminAnyDatabase", "readWriteAnyDatabase"}


def _read_secrets(path: Path = SECRETS_PATH) -> dict:
    try:
        import tomllib

        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _plain(value: Any) -> Any:
    """BSON values to the plain types check_samples validates (ObjectId to its marker type)."""
    if type(value).__name__ == "ObjectId":
        return ObjectId(str(value))
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


Result = tuple[str, str, str]   # (status, check, detail); status is PASS, WARN or FAIL


def check_database(db: Any, expected_counts: dict[str, int] | None = None) -> list[Result]:
    """Checks 3 and 4 against any object shaped like a pymongo Database (so tests can pass a fake)."""
    expected = expected_counts or {name: len(load_documents(name)) for name in COLLECTIONS}
    results: list[Result] = []
    present = set(db.list_collection_names())
    for name in COLLECTIONS:
        if name not in present:
            results.append(("FAIL", f"collection {name}", "missing (load it with mongodb/seed.mongodb.js)"))
            continue
        docs = [_plain(d) for d in db[name].find({})]
        count = len(docs)
        if count == expected[name]:
            results.append(("PASS", f"collection {name}", f"{count} document(s)"))
        else:
            results.append(("WARN", f"collection {name}",
                            f"{count} document(s); the sample file has {expected[name]} (live data may differ)"))
        schema = load_schema(name)
        problems = [m for i, d in enumerate(docs) for m in validate(d, schema, f"{name}[{i}]")]
        if problems:
            results.append(("FAIL", f"{name} schema", f"{len(problems)} problem(s), first: {problems[0]}"))
        else:
            results.append(("PASS", f"{name} schema", "every document satisfies the local schema"))
    return results


def check_privileges(client: Any) -> Result:
    try:
        status = client.admin.command("connectionStatus", showPrivileges=False)
        roles = {r.get("role") for r in status.get("authInfo", {}).get("authenticatedUserRoles", [])}
    except Exception as exc:  # noqa: BLE001
        return ("WARN", "user privileges", f"could not be read ({describe_error(exc)})")
    broad = sorted(roles & _ADMIN_ROLES)
    if broad:
        return ("WARN", "user privileges",
                f"the connecting user has {', '.join(broad)}; give the app a user with only readWrite on janota_fin")
    return ("PASS", "user privileges", f"roles: {', '.join(sorted(r for r in roles if r)) or 'none reported'}")


def run(environ=None, secrets=None, opener: Callable | None = None) -> list[Result]:
    """Every check, as (status, check, detail) rows. ``opener(config)`` returns a client (tests inject a fake)."""
    try:
        config = load_config(environ, _read_secrets() if secrets is None else secrets)
    except MongoConfigError as exc:
        return [("FAIL", "configuration", str(exc))]
    if config is None:
        return [("FAIL", "configuration",
                 "no connection string. Add a [mongodb] block with uri to .streamlit/secrets.toml "
                 "(see secrets.toml.example) or set AUTOREK_MONGODB_URI.")]
    results: list[Result] = [("PASS", "configuration", f"database {config.database!r}, connection string present")]
    if opener is None:
        from .connection import connect as opener  # noqa: PLC0415
    try:
        client = opener(config)
        client.admin.command("ping")
    except ImportError:
        return results + [("FAIL", "driver", "pymongo is not installed: pip install -r requirements.txt")]
    except Exception as exc:  # noqa: BLE001
        return results + [("FAIL", "ping", describe_error(exc))]
    results.append(("PASS", "ping", "the cluster answered"))
    try:
        results += check_database(client[config.database])
    except Exception as exc:  # noqa: BLE001
        results.append(("FAIL", "read", describe_error(exc)))
    results.append(check_privileges(client))
    return results


def main() -> int:
    rows = run()
    for status, check, detail in rows:
        print(f"[{status}] {check}: {detail}")
    failed = any(status == "FAIL" for status, _, _ in rows)
    print("\nReady to connect." if not failed else "\nNot ready: fix the FAIL lines above and run it again.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
