"""The saved-run record: what a finished reconciliation leaves behind for the team and for an auditor.

Streamlit-free and database-free, so it is tested with plain dictionaries. It only copies figures the run already
produced (``metrics``, ``controls``, the upload metadata); it never matches a row, changes a classification, or
touches a workbook. ``run_store`` writes the record to PostgreSQL; ``ui_history`` shows it.

The record keeps fingerprints of the uploads (SHA-256, file name, worksheet), never the files themselves.
``record_sha256`` is a digest of the record's own content, so a stored record that was edited afterwards no longer
verifies (``verify_record``).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from .run_status import MONEY_KEYS, SUMMARY_LABELS  # noqa: F401  (MONEY_KEYS re-exported for the history view)

# metadata key prefix -> stored role, in display order
INPUT_ROLES = (
    ("qb", "qb", "QuickBooks file"),
    ("inf", "infinium", "Infinium file"),
    ("qb_secondary", "qb_historical", "Historical QuickBooks file"),
    ("inf_secondary", "infinium_historical", "Historical Infinium file"),
)
ROLE_LABELS = {role: label for _, role, label in INPUT_ROLES}

# The figures kept on the record: the ones the page already compares between runs, plus the posting verdict.
HEADLINE_KEYS = (*SUMMARY_LABELS, "Posting Status", "Posting Blockers", "Posting Authorization")
UNVERIFIED_NAME = "Unsigned user"


@dataclass(frozen=True)
class Actor:
    """Who did something. ``verified`` is true only for a real sign-in, never for the browser-only one."""
    name: str
    verified: bool = False


UNSIGNED = Actor(UNVERIFIED_NAME, False)


@dataclass(frozen=True)
class InputFingerprint:
    role: str
    filename: str
    sheet_name: Optional[str]
    sha256: str


@dataclass(frozen=True)
class RunRecord:
    run_ref: str
    ran_at: datetime
    ran_by: str
    ran_by_verified: bool
    fiscal_year: Optional[int]
    fiscal_period: Optional[int]
    app_version: str
    matching_rule_version: str
    control_status: str
    posting_status: Optional[str]
    headline: dict[str, Any]
    controls: list[dict[str, Any]]
    mapping: dict[str, Any]
    inputs: list[InputFingerprint] = field(default_factory=list)
    record_sha256: str = ""


def json_safe(value: Any) -> Any:
    """Plain JSON types only: numpy numbers become int/float, NaN and infinity become None, other objects become text."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if hasattr(value, "item") and not isinstance(value, (list, tuple, dict)):
        try:
            return json_safe(value.item())
        except Exception:  # noqa: BLE001
            return str(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _utc_text(moment: datetime) -> str:
    """The instant in UTC, so the digest does not depend on the time zone a database returns it in."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _content(record: RunRecord) -> dict[str, Any]:
    """Everything the digest covers: all fields except the digest itself, in a fixed shape."""
    return {
        "run_ref": record.run_ref,
        "ran_at": _utc_text(record.ran_at),
        "ran_by": record.ran_by,
        "ran_by_verified": record.ran_by_verified,
        "fiscal_year": record.fiscal_year,
        "fiscal_period": record.fiscal_period,
        "app_version": record.app_version,
        "matching_rule_version": record.matching_rule_version,
        "control_status": record.control_status,
        "posting_status": record.posting_status,
        "headline": record.headline,
        "controls": record.controls,
        "mapping": record.mapping,
        "inputs": [{"role": i.role, "filename": i.filename, "sheet_name": i.sheet_name, "sha256": i.sha256}
                   for i in sorted(record.inputs, key=lambda i: i.role)],
    }


def digest_of(record: RunRecord) -> str:
    canonical = json.dumps(_content(record), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_record(record: RunRecord) -> bool:
    """True when the stored digest still matches the record's content."""
    return bool(record.record_sha256) and record.record_sha256 == digest_of(record)


def _input_fingerprints(metadata: Mapping[str, Any]) -> list[InputFingerprint]:
    found = []
    for prefix, role, _ in INPUT_ROLES:
        sha, name = metadata.get(f"{prefix}_sha256"), metadata.get(f"{prefix}_filename")
        if sha and name:
            sheet = metadata.get(f"{prefix}_sheet_name")
            found.append(InputFingerprint(role, str(name), None if sheet is None else str(sheet), str(sha)))
    return found


def _controls_rows(controls: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    keep = ("Check", "Expected", "Actual", "Difference", "Status")
    return [{k: json_safe(row.get(k)) for k in keep} for row in controls]


def build_run_record(*, metadata: Mapping[str, Any], metrics: Mapping[str, Any],
                     controls: Iterable[Mapping[str, Any]], mappings: Mapping[str, Any],
                     app_version: str, matching_rule_version: str, actor: Actor = UNSIGNED) -> RunRecord:
    """The record of a finished run. ``controls`` is the controls table as rows (``DataFrame.to_dict("records")``);
    ``mappings`` holds the column mappings the run used. Raises ValueError for a run with no controls verdict."""
    status = str(metrics.get("Control Status", ""))
    if status not in {"PASS", "FAIL"}:
        raise ValueError("The run has no PASS/FAIL control status, so it cannot be recorded.")
    year, period = metadata.get("fiscal_year"), metadata.get("fiscal_period")
    draft = RunRecord(
        run_ref=str(metadata["run_id"]),
        ran_at=metadata["run_timestamp_dt"],
        ran_by=actor.name or UNVERIFIED_NAME,
        ran_by_verified=bool(actor.verified and actor.name),
        fiscal_year=None if year is None else int(year),
        fiscal_period=None if period in (None, 0) else int(period),
        app_version=app_version,
        matching_rule_version=matching_rule_version,
        control_status=status,
        posting_status=None if metrics.get("Posting Status") is None else str(metrics["Posting Status"]),
        headline={k: json_safe(metrics[k]) for k in HEADLINE_KEYS if k in metrics},
        controls=_controls_rows(controls),
        mapping=json_safe(mappings),
        inputs=_input_fingerprints(metadata),
    )
    return RunRecord(**{**draft.__dict__, "record_sha256": digest_of(draft)})


# ---------------------------------------------------------------------------------------------------------------------
# Export: the run log as a spreadsheet-ready CSV
# ---------------------------------------------------------------------------------------------------------------------
LOG_COLUMNS = (
    "Run", "Ran at", "Ran by", "Sign-in verified", "Fiscal year", "Fiscal period", "Engine version",
    "Matching rules version", "Controls", "Posting status",
    *SUMMARY_LABELS.values(),
    *(f"{label} (SHA-256)" for _, _, label in INPUT_ROLES),
    "Record digest",
)


def log_row(record: RunRecord) -> dict[str, Any]:
    hashes = {i.role: i.sha256 for i in record.inputs}
    row = {
        "Run": record.run_ref, "Ran at": record.ran_at.isoformat(), "Ran by": record.ran_by,
        "Sign-in verified": "yes" if record.ran_by_verified else "no",
        "Fiscal year": record.fiscal_year, "Fiscal period": record.fiscal_period,
        "Engine version": record.app_version, "Matching rules version": record.matching_rule_version,
        "Controls": record.control_status, "Posting status": record.posting_status,
        "Record digest": record.record_sha256,
    }
    for key, label in SUMMARY_LABELS.items():
        row[label] = record.headline.get(key)
    for _, role, label in INPUT_ROLES:
        row[f"{label} (SHA-256)"] = hashes.get(role, "")
    return row


def _csv_safe(value: Any) -> Any:
    """A text cell that Excel would read as a formula gets an apostrophe, as the workpapers do."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return "" if value is None else value


def log_to_csv(records: Iterable[RunRecord]) -> bytes:
    """The run log as UTF-8 CSV with a byte-order mark (so Excel opens it correctly), newest first as given."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=LOG_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    for record in records:
        writer.writerow({k: _csv_safe(v) for k, v in log_row(record).items()})
    return buffer.getvalue().encode("utf-8-sig")
