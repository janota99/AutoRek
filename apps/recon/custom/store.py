"""Shared library of saved mappings in PostgreSQL (tables from db/migrations/001).

Every function takes a connection from `db.connection.tenant_connection`, which has already scoped the transaction to
one organization, and does no commit of its own. A mapping is a template (a name) with immutable, numbered versions:
saving the same mapping again changes nothing, saving a changed one adds a version.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Optional

from .spec import SPEC_VERSION, ReconSpec


@dataclass(frozen=True)
class StoredMapping:
    template_id: Any
    name: str
    category: str
    version: int
    spec: dict


@dataclass(frozen=True)
class StoredVersion:
    template_id: Any
    name: str
    version: int
    spec: dict
    created_at: Any
    created_by: str


@dataclass(frozen=True)
class SaveOutcome:
    template_id: Any
    version: int
    created: bool  # False when the identical mapping was already the latest version


def spec_sha256(spec: ReconSpec) -> str:
    content = {k: v for k, v in asdict(spec).items() if k != "name"}  # the name labels the template, not the rules
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def list_latest(conn) -> list[StoredMapping]:
    rows = conn.execute("SELECT template_id, name, category, version, spec FROM recon_mapping_latest "
                        "ORDER BY lower(name)").fetchall()
    return [StoredMapping(*row) for row in rows]


def list_all_versions(conn) -> list[StoredVersion]:
    """Every version of every library mapping (archived ones excluded), by name then newest version first."""
    rows = conn.execute(
        "SELECT t.template_id, t.name, v.version, v.spec, v.created_at, v.created_by "
        "FROM recon_mapping_templates t JOIN recon_mapping_versions v ON v.template_id = t.template_id "
        "WHERE t.archived_at IS NULL ORDER BY lower(t.name), v.version DESC").fetchall()
    return [StoredVersion(*row) for row in rows]


def save(conn, org_id: str, spec: ReconSpec, *, created_by: str, category: str = "Custom",
         note: Optional[str] = None) -> SaveOutcome:
    from psycopg.types.json import Jsonb

    digest = spec_sha256(spec)
    found = conn.execute("SELECT template_id FROM recon_mapping_templates WHERE lower(name) = lower(%s) "
                         "AND archived_at IS NULL", (spec.name,)).fetchone()
    if found is None:
        template_id = conn.execute(
            "INSERT INTO recon_mapping_templates (org_id, name, category, created_by) VALUES (%s, %s, %s, %s) "
            "RETURNING template_id", (org_id, spec.name, category, created_by)).fetchone()[0]
        version = 1
    else:
        template_id = found[0]
        latest = conn.execute("SELECT version, spec_sha256 FROM recon_mapping_versions WHERE template_id = %s "
                              "ORDER BY version DESC LIMIT 1", (template_id,)).fetchone()
        if latest and latest[1] == digest:
            return SaveOutcome(template_id, latest[0], False)
        version = (latest[0] if latest else 0) + 1
    conn.execute(
        "INSERT INTO recon_mapping_versions (template_id, org_id, version, spec, spec_version, spec_sha256, label_a, "
        "label_b, max_a_per_b, max_b_per_a, align_count, change_note, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (template_id, org_id, version, Jsonb(asdict(spec)), SPEC_VERSION, digest, spec.label_a, spec.label_b,
         spec.max_a_per_b, spec.max_b_per_a, len(spec.align), note, created_by))
    return SaveOutcome(template_id, version, True)


def versions(conn, template_id) -> list[tuple[int, Any, str]]:
    """(version, created_at, created_by) for a template, newest first."""
    return conn.execute("SELECT version, created_at, created_by FROM recon_mapping_versions WHERE template_id = %s "
                        "ORDER BY version DESC", (template_id,)).fetchall()


def archive(conn, template_id) -> None:
    """Hide a template from the library. Its versions stay, so past runs can still name the mapping they used."""
    conn.execute("UPDATE recon_mapping_templates SET archived_at = now() WHERE template_id = %s", (template_id,))
