"""Saved Recon runs in PostgreSQL (tables from db/migrations/004).

Same contract as ``custom/store.py``: every function takes a connection from ``db.connection.tenant_connection``, which
has already scoped the transaction to one organization, and does no commit of its own. Everything here appends; the
database refuses updates and deletes of these tables, so a saved run, its event log, and its workpapers are history.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Optional

from .run_log import Actor, InputFingerprint, RunRecord, verify_record

EVENT_TYPES = ("note", "reviewed", "workpaper_prepared", "workpaper_downloaded")
WORKPAPER_KINDS = ("primary", "legacy")
MAX_NOTE_CHARS = 2000

_RUN_COLUMNS = ("run_id, run_ref, ran_at, ran_by, ran_by_verified, fiscal_year, fiscal_period, app_version, "
                "matching_rule_version, control_status, posting_status, headline, controls, mapping, record_sha256")


@dataclass(frozen=True)
class SavedRun:
    run_id: Any
    record: RunRecord
    intact: bool  # the stored digest still matches the stored content


@dataclass(frozen=True)
class RunEvent:
    event_id: Any
    run_id: Any
    event_type: str
    actor: str
    actor_verified: bool
    detail: dict
    created_at: Any


@dataclass(frozen=True)
class WorkpaperInfo:
    workpaper_id: Any
    run_id: Any
    kind: str
    filename: str
    sha256: str
    size_bytes: int
    created_by: str
    created_at: Any


@dataclass(frozen=True)
class SaveResult:
    run_id: Any
    created: bool  # False when this run reference was already saved (nothing is written twice)


def save_run(conn, org_id: str, record: RunRecord) -> SaveResult:
    """Append the run and its input fingerprints. Saving the same run reference again changes nothing."""
    from psycopg.types.json import Jsonb

    if not verify_record(record):
        raise ValueError("The run record's digest does not match its content, so it was not saved.")
    found = conn.execute("SELECT run_id FROM recon_runs WHERE run_ref = %s", (record.run_ref,)).fetchone()
    if found is not None:
        return SaveResult(found[0], False)
    run_id = conn.execute(
        "INSERT INTO recon_runs (org_id, run_ref, ran_at, ran_by, ran_by_verified, fiscal_year, fiscal_period, "
        "app_version, matching_rule_version, control_status, posting_status, headline, controls, mapping, "
        "record_sha256) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING run_id",
        (org_id, record.run_ref, record.ran_at, record.ran_by, record.ran_by_verified, record.fiscal_year,
         record.fiscal_period, record.app_version, record.matching_rule_version, record.control_status,
         record.posting_status, Jsonb(record.headline), Jsonb(record.controls), Jsonb(record.mapping),
         record.record_sha256)).fetchone()[0]
    for item in record.inputs:
        conn.execute("INSERT INTO recon_run_inputs (org_id, run_id, role, filename, sheet_name, sha256) "
                     "VALUES (%s, %s, %s, %s, %s, %s)",
                     (org_id, run_id, item.role, item.filename, item.sheet_name, item.sha256))
    return SaveResult(run_id, True)


def _inputs_by_run(conn, run_ids: list) -> dict:
    if not run_ids:
        return {}
    rows = conn.execute("SELECT run_id, role, filename, sheet_name, sha256 FROM recon_run_inputs "
                        "WHERE run_id = ANY(%s) ORDER BY role", (run_ids,)).fetchall()
    out: dict = {}
    for run_id, role, filename, sheet, sha in rows:
        out.setdefault(run_id, []).append(InputFingerprint(role, filename, sheet, sha))
    return out


def _saved(row, inputs) -> SavedRun:
    (run_id, run_ref, ran_at, ran_by, verified, year, period, app_v, rules_v, control, posting, headline, controls,
     mapping, digest) = row
    record = RunRecord(run_ref=run_ref, ran_at=ran_at, ran_by=ran_by, ran_by_verified=verified, fiscal_year=year,
                       fiscal_period=period, app_version=app_v, matching_rule_version=rules_v,
                       control_status=control, posting_status=posting, headline=headline, controls=controls,
                       mapping=mapping, inputs=inputs, record_sha256=digest)
    return SavedRun(run_id, record, verify_record(record))


def list_runs(conn, *, limit: int = 100) -> list[SavedRun]:
    """The most recent runs first, each with its input fingerprints."""
    rows = conn.execute(f"SELECT {_RUN_COLUMNS} FROM recon_runs ORDER BY recorded_at DESC, run_id LIMIT %s",
                        (max(1, int(limit)),)).fetchall()
    inputs = _inputs_by_run(conn, [r[0] for r in rows])
    return [_saved(r, inputs.get(r[0], [])) for r in rows]


def get_run(conn, run_id) -> Optional[SavedRun]:
    row = conn.execute(f"SELECT {_RUN_COLUMNS} FROM recon_runs WHERE run_id = %s", (run_id,)).fetchone()
    return None if row is None else _saved(row, _inputs_by_run(conn, [run_id]).get(run_id, []))


def add_event(conn, org_id: str, run_id, event_type: str, actor: Actor, detail: Optional[dict] = None) -> Any:
    """Append a line to a run's log. A note needs text; the other types carry whatever detail the caller gives."""
    from psycopg.types.json import Jsonb

    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown event type: {event_type}")
    detail = dict(detail or {})
    if event_type == "note":
        text = str(detail.get("text", "")).strip()
        if not text:
            raise ValueError("A note cannot be empty.")
        detail["text"] = text[:MAX_NOTE_CHARS]
    return conn.execute(
        "INSERT INTO recon_run_events (org_id, run_id, event_type, actor, actor_verified, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING event_id",
        (org_id, run_id, event_type, actor.name, bool(actor.verified), Jsonb(detail))).fetchone()[0]


def list_events(conn, run_id) -> list[RunEvent]:
    rows = conn.execute("SELECT event_id, run_id, event_type, actor, actor_verified, detail, created_at "
                        "FROM recon_run_events WHERE run_id = %s ORDER BY seq", (run_id,)).fetchall()
    return [RunEvent(*row) for row in rows]


def save_workpaper(conn, org_id: str, run_id, *, kind: str, filename: str, content: bytes, actor: Actor) -> Any:
    """Keep the workpaper built for a run, and log that it was prepared."""
    if kind not in WORKPAPER_KINDS:
        raise ValueError(f"Unknown workpaper kind: {kind}")
    if not content:
        raise ValueError("The workpaper is empty, so it was not saved.")
    digest = hashlib.sha256(content).hexdigest()
    workpaper_id = conn.execute(
        "INSERT INTO recon_run_workpapers (org_id, run_id, kind, filename, sha256, size_bytes, content, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING workpaper_id",
        (org_id, run_id, kind, filename, digest, len(content), content, actor.name)).fetchone()[0]
    add_event(conn, org_id, run_id, "workpaper_prepared", actor, {"kind": kind, "filename": filename, "sha256": digest})
    return workpaper_id


def list_workpapers(conn, run_id) -> list[WorkpaperInfo]:
    """Newest first; the file bytes are not read here."""
    rows = conn.execute("SELECT workpaper_id, run_id, kind, filename, sha256, size_bytes, created_by, created_at "
                        "FROM recon_run_workpapers WHERE run_id = %s ORDER BY created_at DESC, workpaper_id", (run_id,)).fetchall()
    return [WorkpaperInfo(*row) for row in rows]


def read_workpaper(conn, workpaper_id) -> Optional[tuple[WorkpaperInfo, bytes]]:
    """The stored file, checked against its recorded SHA-256 (a mismatch raises rather than serving altered bytes)."""
    row = conn.execute("SELECT workpaper_id, run_id, kind, filename, sha256, size_bytes, created_by, created_at, "
                       "content FROM recon_run_workpapers WHERE workpaper_id = %s", (workpaper_id,)).fetchone()
    if row is None:
        return None
    content = bytes(row[8])
    if hashlib.sha256(content).hexdigest() != row[4]:
        raise ValueError("The stored workpaper no longer matches its recorded SHA-256, so it was not served.")
    return WorkpaperInfo(*row[:8]), content
