"""
Ingestion utilities for the Sales Tax Vendor Review Tool.

This module owns file reading, Trial Balance cache management, and parsing
excluded vendor IDs. It deliberately contains no Streamlit dependency so the
functions can be exercised in ordinary unit tests.

The Trial Balance cache is shared by default. In a multi-user deployment,
access to replace or clear it should be restricted in the UI to authorized
administrators. Local disk also may not survive a hosting restart, so this is
a convenience cache rather than a permanent system of record. Atomic writes
protect against a corrupted file from a failed/concurrent write, and a
one-version backup gives an undo path - neither of those is the same thing
as per-user isolation or access control, which remain a UI-layer TODO.

Blank-vs-conflict rule for duplicate GL accounts / vendor entries: a blank
value on one duplicate row is an INCOMPLETE entry, not a disagreement, and
never conflicts with a real value on another row sharing the same key. Only
two or more DIFFERENT non-blank values sharing a key are a genuine conflict.
This module's _validate_trial_balance applies that rule; cleanup.py's own
mapping/trial-balance conflict detectors and duplicate-consolidation logic
apply the identical rule independently for the same reason.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pandas as pd


CACHE_DIR = Path(__file__).resolve().parent / "data"
TRIAL_BALANCE_CACHE_PATH = CACHE_DIR / "trial_balance_cache.xlsx"
TRIAL_BALANCE_BACKUP_PATH = CACHE_DIR / "trial_balance_cache.backup.xlsx"

MAX_EXCEL_BYTES = 25 * 1024 * 1024
MAX_EXCLUSION_CSV_BYTES = 2 * 1024 * 1024
MIN_TRIAL_BALANCE_COLUMNS = 3

_WHOLE_NUMBER_FLOAT_RE = re.compile(r"^(\d+)\.0+$")
_LIKELY_EXCLUSION_HEADERS = {
    "VENDOR",
    "VENDORID",
    "VENDORNUMBER",
    "VENDORNO",
    "ID",
}


class IngestionError(ValueError):
    """A user-correctable upload or cache error."""


@dataclass(frozen=True)
class CacheStatus:
    """Non-sensitive metadata describing the shared Trial Balance cache."""

    exists: bool
    valid: bool
    modified_at: datetime | None = None
    fingerprint: str | None = None
    row_count: int | None = None
    # GL accounts present in the file where every occurrence has a blank
    # Account Name. Not an error - just worth surfacing, since a file full
    # of unclassified accounts could otherwise look identical to a fully
    # validated one in the UI.
    incomplete_count: int = 0
    error: str | None = None


def _read_file_bytes(file_or_bytes, *, max_bytes: int, label: str) -> bytes:
    """Return upload bytes without depending on a specific UI framework."""
    if file_or_bytes is None:
        raise IngestionError(f"{label}: no file was supplied.")

    if isinstance(file_or_bytes, (bytes, bytearray, memoryview)):
        data = bytes(file_or_bytes)
    elif hasattr(file_or_bytes, "getvalue"):
        data = bytes(file_or_bytes.getvalue())
    elif hasattr(file_or_bytes, "read"):
        stream: BinaryIO = file_or_bytes
        original_position = None
        try:
            if hasattr(stream, "tell"):
                original_position = stream.tell()
            if hasattr(stream, "seek"):
                stream.seek(0)
            data = stream.read()
        finally:
            if original_position is not None and hasattr(stream, "seek"):
                stream.seek(original_position)
        if isinstance(data, str):
            data = data.encode("utf-8")
        data = bytes(data)
    else:
        raise IngestionError(f"{label}: unsupported file object.")

    if not data:
        raise IngestionError(f"{label}: the file is empty.")
    if len(data) > max_bytes:
        raise IngestionError(
            f"{label}: the file is {len(data) / (1024 * 1024):,.1f} MB; "
            f"the maximum allowed size is {max_bytes / (1024 * 1024):,.0f} MB."
        )
    return data


def _read_excel_bytes(data: bytes, *, label: str, sheet_name=0) -> pd.DataFrame:
    try:
        df = pd.read_excel(BytesIO(data), header=0, sheet_name=sheet_name)
    except (ValueError, ImportError, OSError) as exc:
        raise IngestionError(
            f"{label}: the workbook could not be read as an Excel file."
        ) from exc
    except Exception as exc:
        # Parser implementations may raise library-specific exception types.
        # Convert these to one stable, user-facing application exception while
        # preserving the original exception for logs and debugging.
        raise IngestionError(
            f"{label}: an unexpected workbook-reading error occurred."
        ) from exc

    if not isinstance(df, pd.DataFrame):
        raise IngestionError(f"{label}: select exactly one worksheet.")
    return df


def read_excel_upload(uploaded_file, sheet_name=0) -> pd.DataFrame:
    """Read one uploaded Excel worksheet into a DataFrame.

    Raises IngestionError for empty, oversized, unreadable, or invalid uploads.
    Business-specific schema validation remains in cleanup.py.
    """
    data = _read_file_bytes(
        uploaded_file,
        max_bytes=MAX_EXCEL_BYTES,
        label="Excel upload",
    )
    return _read_excel_bytes(data, label="Excel upload", sheet_name=sheet_name)


def _normalize_identifier(value) -> str:
    """Normalize spreadsheet identifiers while preserving textual zeroes."""
    if pd.isna(value):
        return ""
    text = (
        str(value)
        .strip()
        .replace("\xa0", "")
        .replace(" ", "")
        .strip('"')
        .strip("'")
    )
    match = _WHOLE_NUMBER_FLOAT_RE.fullmatch(text)
    if match:
        text = match.group(1)
    return text.upper()


def _validate_trial_balance(df: pd.DataFrame) -> int:
    """Validate the positional Trial Balance contract used by cleanup.py.

    Returns the count of GL accounts where every occurrence has a blank
    Account Name - allowed, but worth reporting as incomplete rather than
    silently treated the same as a fully validated file.

    Raises IngestionError only for genuine problems: an empty file, too
    few columns, no usable GL accounts at all, or a GL account with two or
    more DIFFERENT non-blank Account Names. A blank Account Name on one
    duplicate row never conflicts with a real name on another row sharing
    the same GL account - that's an incomplete entry, not a disagreement.
    """
    if df.empty:
        raise IngestionError("Trial Balance: the workbook contains no data rows.")
    if df.shape[1] < MIN_TRIAL_BALANCE_COLUMNS:
        raise IngestionError(
            f"Trial Balance: found {df.shape[1]} column(s); at least "
            f"{MIN_TRIAL_BALANCE_COLUMNS} are required."
        )

    account_names = df.iloc[:, 1].fillna("").astype(str).str.strip()
    account_keys = df.iloc[:, 2].map(_normalize_identifier)
    if not account_keys.ne("").any():
        raise IngestionError(
            "Trial Balance: column 3 does not contain any usable GL accounts."
        )

    populated = pd.DataFrame({"key": account_keys, "name": account_names})
    populated = populated[populated["key"] != ""].copy()
    populated["_name_key"] = populated["name"].str.upper()

    nonblank_names = populated[populated["_name_key"] != ""]
    conflicts = (
        nonblank_names.groupby("key", sort=False)["_name_key"]
        .nunique()
        .loc[lambda counts: counts > 1]
    )
    if not conflicts.empty:
        examples = ", ".join(conflicts.index.astype(str).tolist()[:10])
        remainder = len(conflicts) - min(len(conflicts), 10)
        suffix = f" (+{remainder} more)" if remainder else ""
        raise IngestionError(
            "Trial Balance: conflicting account names were found for duplicate "
            f"GL account(s): {examples}{suffix}."
        )

    all_keys = set(populated["key"].unique())
    named_keys = set(nonblank_names["key"].unique())
    return len(all_keys - named_keys)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cache_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _load_cache_snapshot() -> tuple[pd.DataFrame, CacheStatus]:
    """Reads the cache file exactly once and returns (df, CacheStatus) built
    from that same byte snapshot - guarantees the dataframe, fingerprint,
    and validity all describe identical bytes, and avoids parsing the file
    twice per call (previously this happened once inside
    get_trial_balance_cache_status() and again directly in
    load_cached_trial_balance()).

    Raises IngestionError/OSError on any problem; callers decide how to
    handle that (load_cached_trial_balance treats it as "no usable cache",
    get_trial_balance_cache_status surfaces it as the specific error)."""
    data = TRIAL_BALANCE_CACHE_PATH.read_bytes()
    if not data:
        raise IngestionError("Cached Trial Balance: the cache file is empty.")
    if len(data) > MAX_EXCEL_BYTES:
        raise IngestionError("Cached Trial Balance: the cache file is oversized.")
    df = _read_excel_bytes(data, label="Cached Trial Balance")
    incomplete_count = _validate_trial_balance(df)
    status = CacheStatus(
        exists=True,
        valid=True,
        modified_at=_cache_mtime(TRIAL_BALANCE_CACHE_PATH),
        fingerprint=_sha256(data),
        row_count=len(df),
        incomplete_count=incomplete_count,
    )
    return df, status


def load_trial_balance_cache_snapshot() -> tuple[pd.DataFrame | None, CacheStatus]:
    """Return the cached DataFrame and its status from one byte snapshot.

    This is the preferred API for the Streamlit UI because it avoids calling
    a status function and a data-loading function separately during the same
    rerun. Missing and invalid caches return ``None`` for the DataFrame and a
    status containing the reason.
    """
    if not TRIAL_BALANCE_CACHE_PATH.exists():
        return None, CacheStatus(
            exists=False,
            valid=False,
            error="No Trial Balance is cached.",
        )
    try:
        return _load_cache_snapshot()
    except (IngestionError, OSError) as exc:
        return None, CacheStatus(exists=True, valid=False, error=str(exc))


def load_cached_trial_balance():
    """Return ``(dataframe, modified_at)`` for backward compatibility."""
    df, status = load_trial_balance_cache_snapshot()
    if not status.valid:
        return None, None
    return df, status.modified_at


def get_trial_balance_cache_status() -> CacheStatus:
    """Inspect the cache and return metadata plus a safe error message."""
    _, status = load_trial_balance_cache_snapshot()
    return status


def _write_private_file_atomically(destination: Path, data: bytes) -> None:
    """Write bytes beside the target and atomically replace the target."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.parent.chmod(0o700)
    except OSError:
        # Some platforms do not support POSIX permission bits.
        pass

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as temporary_file:
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        try:
            temporary_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary_path, destination)
        try:
            destination.chmod(0o600)
        except OSError:
            pass
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def save_trial_balance_cache(file_bytes: bytes) -> CacheStatus:
    """Validate and atomically replace the shared Trial Balance cache.

    The current valid cache is retained as a one-version backup. Nothing is
    replaced if the new workbook fails validation. Raises IngestionError on
    validation failure - callers MUST catch this (see the Trial
    Balance upload handlers in transaction_cleanup.py), since it can legitimately fail on a bad file.
    """
    data = _read_file_bytes(
        file_bytes,
        max_bytes=MAX_EXCEL_BYTES,
        label="Trial Balance",
    )
    df = _read_excel_bytes(data, label="Trial Balance")
    incomplete_count = _validate_trial_balance(df)

    if TRIAL_BALANCE_CACHE_PATH.exists():
        current_status = get_trial_balance_cache_status()
        if current_status.valid:
            current_data = TRIAL_BALANCE_CACHE_PATH.read_bytes()
            _write_private_file_atomically(TRIAL_BALANCE_BACKUP_PATH, current_data)

    _write_private_file_atomically(TRIAL_BALANCE_CACHE_PATH, data)
    return CacheStatus(
        exists=True,
        valid=True,
        modified_at=_cache_mtime(TRIAL_BALANCE_CACHE_PATH),
        fingerprint=_sha256(data),
        row_count=len(df),
        incomplete_count=incomplete_count,
    )


def restore_trial_balance_cache_backup() -> CacheStatus:
    """Restore the last valid cache version, retaining the current one."""
    if not TRIAL_BALANCE_BACKUP_PATH.exists():
        raise IngestionError("No Trial Balance cache backup is available.")

    backup_data = TRIAL_BALANCE_BACKUP_PATH.read_bytes()
    backup_df = _read_excel_bytes(backup_data, label="Trial Balance backup")
    incomplete_count = _validate_trial_balance(backup_df)

    current_data = (
        TRIAL_BALANCE_CACHE_PATH.read_bytes()
        if TRIAL_BALANCE_CACHE_PATH.exists()
        else None
    )
    _write_private_file_atomically(TRIAL_BALANCE_CACHE_PATH, backup_data)
    if current_data:
        _write_private_file_atomically(TRIAL_BALANCE_BACKUP_PATH, current_data)

    return CacheStatus(
        exists=True,
        valid=True,
        modified_at=_cache_mtime(TRIAL_BALANCE_CACHE_PATH),
        fingerprint=_sha256(backup_data),
        row_count=len(backup_df),
        incomplete_count=incomplete_count,
    )


def clear_trial_balance_cache(*, include_backup: bool = True) -> int:
    """Remove cached Trial Balance files and return the number removed."""
    targets = [TRIAL_BALANCE_CACHE_PATH]
    if include_backup:
        targets.append(TRIAL_BALANCE_BACKUP_PATH)

    removed = 0
    for path in targets:
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise IngestionError("The Trial Balance cache could not be cleared.") from exc
    return removed


def parse_excluded_ids(text: str, csv_file=None):
    """Return normalized, unique excluded vendor IDs and an optional error.

    Text accepts comma- or newline-separated values. The optional CSV should
    contain one ID per row. Blank values, numeric ``.0`` artifacts, duplicate
    IDs, and a likely first-row header are removed. Text IDs are still returned
    when the CSV cannot be read.
    """
    raw_text_ids = re.split(r"[,\r\n]+", text or "")
    normalized_ids = []
    seen = set()

    def add_values(values) -> None:
        for value in values:
            key = _normalize_identifier(value)
            if not key or key in seen:
                continue
            seen.add(key)
            normalized_ids.append(key)

    add_values(raw_text_ids)

    if csv_file is None:
        return normalized_ids, None

    try:
        csv_bytes = _read_file_bytes(
            csv_file,
            max_bytes=MAX_EXCLUSION_CSV_BYTES,
            label="Exclusion CSV",
        )
        exclusion_df = pd.read_csv(BytesIO(csv_bytes), header=None, dtype=object)
        if exclusion_df.shape[1] != 1:
            return normalized_ids, (
                "Exclusion CSV: exactly one column of Vendor IDs is required."
            )

        csv_values = exclusion_df.iloc[:, 0].dropna().tolist()
        if csv_values:
            first_key = _normalize_identifier(csv_values[0])
            if first_key in _LIKELY_EXCLUSION_HEADERS:
                csv_values = csv_values[1:]
        add_values(csv_values)
        return normalized_ids, None
    except (IngestionError, pd.errors.ParserError, UnicodeError, OSError) as exc:
        return normalized_ids, str(exc)
    except Exception:
        return normalized_ids, "Exclusion CSV: the file could not be read."
