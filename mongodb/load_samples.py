"""Loads the sample documents into Atlas, without needing mongosh.

    py -m mongodb.load_samples            # creates what is missing; never overwrites existing data
    py -m mongodb.load_samples --reset    # drops and recreates reviews, plans, tools and taxRules first

Uses the same settings as ``py -m mongodb.check_connection``. Each collection is created with its
``$jsonSchema`` validator from ``mongodb/schemas/`` (strict, so the server rejects a wrong type, a missing
field, or an extra field), then the sample documents are inserted. Without ``--reset`` a collection that
already holds documents is left alone, so running this twice cannot duplicate or destroy anything.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .check_connection import _read_secrets
from .check_samples import COLLECTIONS
from .connection import MongoConfigError, connect, describe_error, load_config

HERE = Path(__file__).resolve().parent


def load(db: Any, *, reset: bool = False) -> list[str]:
    """Create and fill the collections on any object shaped like a pymongo Database; returns report lines."""
    from bson import json_util  # ships with pymongo; extended JSON ($oid, $date) to real BSON types

    report: list[str] = []
    existing = set(db.list_collection_names())
    for name in COLLECTIONS:
        if name in existing and reset:
            db[name].drop()
            existing.discard(name)
        if name in existing:
            count = db[name].count_documents({})
            if count:
                report.append(f"{name}: skipped, already holds {count} document(s) (use --reset to replace)")
                continue
        else:
            validator = json.loads((HERE / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))
            db.create_collection(name, validator=validator, validationLevel="strict", validationAction="error")
        docs = json_util.loads((HERE / f"{name}.json").read_text(encoding="utf-8"))
        db[name].insert_many(docs)
        report.append(f"{name}: inserted {len(docs)} document(s)")
    return report


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    reset = "--reset" in argv
    try:
        config = load_config(secrets=_read_secrets())
    except MongoConfigError as exc:
        print(f"[FAIL] configuration: {exc}")
        return 1
    if config is None:
        print("[FAIL] configuration: no [mongodb] settings found in .streamlit/secrets.toml")
        return 1
    try:
        db = connect(config)[config.database]
        for line in load(db, reset=reset):
            print(line)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {describe_error(exc)}")
        return 1
    print("\nDone. Verify with: py -m mongodb.check_connection")
    return 0


if __name__ == "__main__":
    sys.exit(main())
