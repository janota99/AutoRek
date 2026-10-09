"""
Persisted, user-configurable reconciliation tolerances.

QTY_TOLERANCE, VALUE_TOLERANCE, ZERO_COST_TOLERANCE, and the accepted
Known-Value-Variance-Drift band all started life as constants baked into
Python source (user_inputs.py and excel_export.py), which meant changing
any of them required editing code and redeploying. This module makes them
adjustable from within the app itself: it reads/writes a small JSON file on
disk (using the same atomic-write + read-back-verify pattern used for FIFO
snapshots in fifo_layer_store.py), and falls back to the original hardcoded
defaults whenever no override has been saved, or whenever the saved file is
missing, unreadable, or fails validation.

Scope note: this governs the tolerances used by the REVIEW/FAIL judgment
checks in excel_export.py (_compute_product_controls /
_compute_summary_control_status) and the Known Value Variance Drift check.
It deliberately does NOT change the near-zero epsilon guards used
internally by fifo_layer_store.py for basic data validation (rejecting a
negative layer quantity, verifying a restored snapshot's control totals
still match its layers). Those are structural data-integrity guards against
floating-point noise and corrupted input, not reconciliation judgment
thresholds — loosening them would weaken data-safety checks rather than
tune a business judgment call, so they stay fixed in code.
"""
import json
import os
from pathlib import Path

from .user_inputs import QTY_TOLERANCE, VALUE_TOLERANCE, ZERO_COST_TOLERANCE

# Where the settings override file lives. Override with an env var for the
# same reason FIFO_SNAPSHOT_DIR is overridable in fifo_layer_store.py — so
# this can point at a durable path if the app's working directory isn't one.
# The default sits next to this module, not the process's working directory,
# because the combined app is launched from the parent folder.
SETTINGS_PATH = Path(os.environ.get(
    "FIFO_SETTINGS_PATH", Path(__file__).resolve().parent / "app_settings.json"
))

DEFAULT_TOLERANCES = {
    'qty_tolerance': float(QTY_TOLERANCE),
    'value_tolerance': float(VALUE_TOLERANCE),
    'zero_cost_tolerance': float(ZERO_COST_TOLERANCE),
}

_KEYS = tuple(DEFAULT_TOLERANCES.keys())

_LABELS = {
    'qty_tolerance': "Quantity Tolerance (units)",
    'value_tolerance': "Value Tolerance ($)",
    'zero_cost_tolerance': "Zero-Cost Layer Threshold ($/unit)",
}

_HELP = {
    'qty_tolerance': "How close a quantity comparison must be to count as tied out, in units. Used for "
                      "Beginning/Receipt/Depletion Quantity Agreement and Ending Quantity to Open Layers.",
    'value_tolerance': "How close a dollar comparison must be to count as tied out. Used for Receipt Value "
                        "Agreement, Layer Value Conservation, Ending Value to Open Layers, and the dollar effect "
                        "of a beginning quantity variance.",
    'zero_cost_tolerance': "A layer's unit cost at or below this is treated as unpriced/zero-cost for the "
                            "'Unpriced Layer Count' review flag and the Executive Summary's zero-cost footnote.",
}


def _validate(tolerances):
    for key in _KEYS:
        if key not in tolerances:
            raise ValueError(f"Settings are missing required field: {key}")
        value = tolerances[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{key} must be numeric.")
        if value != value or value in (float('inf'), float('-inf')):  # NaN / inf guard
            raise ValueError(f"{key} must be a finite number.")
    for key in ('qty_tolerance', 'value_tolerance', 'zero_cost_tolerance'):
        if tolerances[key] < 0:
            raise ValueError(f"{_LABELS[key]} cannot be negative.")
    return {key: float(tolerances[key]) for key in _KEYS}


def load_settings():
    """Returns the persisted overrides merged over the hardcoded defaults.
    Any field missing from a saved file (e.g. one saved before a new
    tolerance existed) is filled in from the default rather than raising,
    so this app can add tolerances later without breaking an older settings
    file. A corrupt or unreadable file falls back to defaults entirely
    rather than crashing the app or silently applying a half-broken value."""
    if not SETTINGS_PATH.exists():
        return dict(DEFAULT_TOLERANCES)
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
        merged = dict(DEFAULT_TOLERANCES)
        merged.update({k: raw[k] for k in _KEYS if k in raw})
        return _validate(merged)
    except Exception:
        return dict(DEFAULT_TOLERANCES)


def save_settings(tolerances):
    """Validates and atomically persists a full set of tolerance overrides.
    Raises on invalid input or on a failed read-back verification, rather
    than silently leaving a partially-written or unverified file behind."""
    validated = _validate(tolerances)
    tmp_path = SETTINGS_PATH.with_suffix('.json.tmp')
    tmp_path.write_text(json.dumps(validated, indent=2), encoding='utf-8')
    tmp_path.replace(SETTINGS_PATH)  # atomic on POSIX/NTFS

    written = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
    if _validate(written) != validated:
        raise IOError(f"Settings written to {SETTINGS_PATH} failed read-back verification.")
    return validated


def reset_to_defaults():
    if SETTINGS_PATH.exists():
        SETTINGS_PATH.unlink()
    return dict(DEFAULT_TOLERANCES)


def get_effective_tolerances():
    """The tolerances currently in effect — what every REVIEW/FAIL check in
    excel_export.py uses. Always re-reads from disk (a small, cheap local
    file) so a change saved from the Settings tab takes effect immediately
    on the very next preview or export, with no app restart needed."""
    return load_settings()


def is_overridden():
    """Whether any tolerance currently differs from the hardcoded defaults."""
    current = load_settings()
    return any(current[k] != DEFAULT_TOLERANCES[k] for k in _KEYS)


def field_label(key):
    return _LABELS.get(key, key)


def field_help(key):
    return _HELP.get(key, "")