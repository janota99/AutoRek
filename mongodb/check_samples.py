"""Checks the sample documents against the collection schemas, with no MongoDB server needed.

Run from the repository root:  py mongodb/check_samples.py   (or `py -m pytest mongodb`).

The schemas in schemas/ are MongoDB `$jsonSchema` validators (they use `bsonType`). This is a small
stand-in for the server's own validation that understands just the keywords those schemas use, so a
type mistake in a sample document is caught here before the data is ever loaded.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
COLLECTIONS = ("reviews", "plans", "tools")


class ObjectId(str):
    """Marker type for `{"$oid": ...}` in extended JSON."""


def load_documents(collection: str) -> list[dict]:
    return _decode(json.loads((HERE / f"{collection}.json").read_text(encoding="utf-8")))


def load_schema(collection: str) -> dict:
    return json.loads((HERE / "schemas" / f"{collection}.schema.json").read_text(encoding="utf-8"))["$jsonSchema"]


def _decode(value):
    """Extended JSON to Python: {"$oid"} -> ObjectId, {"$date"} -> datetime."""
    if isinstance(value, dict):
        if set(value) == {"$oid"}:
            return ObjectId(value["$oid"])
        if set(value) == {"$date"}:
            return datetime.fromisoformat(value["$date"].replace("Z", "+00:00"))
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def _bson_type(value) -> set[str]:
    """The bsonType names a Python value satisfies. bool is checked first: it is also an int in Python."""
    if value is None:
        return {"null"}
    if isinstance(value, bool):
        return {"bool"}
    if isinstance(value, (int, float)):
        return {"number"}
    if isinstance(value, ObjectId):
        return {"objectId"}
    if isinstance(value, str):
        return {"string"}
    if isinstance(value, datetime):
        return {"date"}
    if isinstance(value, list):
        return {"array"}
    if isinstance(value, dict):
        return {"object"}
    return {type(value).__name__}


def validate(value, schema: dict, path: str = "$") -> list[str]:
    """Every way `value` breaks `schema`, as readable messages (empty list = valid)."""
    errors: list[str] = []
    wanted = schema.get("bsonType")
    if wanted is not None:
        allowed = {wanted} if isinstance(wanted, str) else set(wanted)
        if not (_bson_type(value) & allowed):
            return [f"{path}: expected {sorted(allowed)}, got {sorted(_bson_type(value))} ({value!r})"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below the minimum of {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above the maximum of {schema['maximum']}")
        if "multipleOf" in schema and value % schema["multipleOf"] != 0:
            errors.append(f"{path}: not a multiple of {schema['multipleOf']}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors += validate(item, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing required field '{name}'")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in props:
                    errors.append(f"{path}: unexpected field '{name}'")
        for name, sub in props.items():
            if name in value:
                errors += validate(value[name], sub, f"{path}.{name}")
    return errors


def check_all() -> dict[str, list[str]]:
    problems = {}
    for name in COLLECTIONS:
        schema = load_schema(name)
        found = []
        for i, doc in enumerate(load_documents(name)):
            found += validate(doc, schema, f"{name}[{i}]")
        problems[name] = found
    return problems


if __name__ == "__main__":
    failed = False
    for name, found in check_all().items():
        docs = len(load_documents(name))
        print(f"{name}: {docs} document(s), {'OK' if not found else str(len(found)) + ' problem(s)'}")
        for message in found:
            print("  -", message)
            failed = True
    sys.exit(1 if failed else 0)
