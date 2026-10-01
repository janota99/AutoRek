"""
FIFO layer data model and persistence.

This module owns what a FIFO inventory layer *is* and how the running set of
layers is stored, validated, and carried across fiscal periods:
  - `_canonicalize_layer` / `_layer_control_totals`: normalize and total a
    layer or set of layers (shared by the store and by the calculation
    engine in fifo_calculations.py).
  - `FIFOLayerStore`: the session-state-backed store of record. It handles
    reading/writing the current layers, the known beginning-value-variance
    baseline carried alongside them, JSON snapshot export/import (with
    schema + control-total validation), the atomic period lifecycle
    (commit_period / reopen_latest / period_is_closed / sequence_error),
    and disk-backed durability so committed periods survive a session loss.

Nothing in this module performs FIFO consumption math (that lives in
fifo_calculations.py) and nothing in it renders UI (that lives in app.py).
It still reads/writes `st.session_state` directly, since that is this app's
in-memory working state — disk is the durability layer underneath it.
"""
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from .user_inputs import ENGINE_VERSION, SNAPSHOT_SCHEMA_VERSION, QTY_TOLERANCE, VALUE_TOLERANCE
from .ingestion import _finite_number

# Where auto-saved snapshots live. Override with an env var if this app ever
# runs somewhere the process's working directory isn't durable (e.g. point
# it at a synced Dropbox/OneDrive folder, or a mounted volume) — otherwise
# it defaults to a folder next to this module (not the working directory,
# since the combined app is launched from the parent folder).
SNAPSHOT_DIR = Path(os.environ.get(
    "FIFO_SNAPSHOT_DIR", Path(__file__).resolve().parent / "fifo_snapshots"
))


def _canonicalize_layer(layer):
    if not isinstance(layer, dict):
        raise ValueError("Every FIFO layer must be a JSON object.")
    qty = _finite_number(layer.get('qty', 0), 'Layer quantity')
    if qty < -QTY_TOLERANCE:
        raise ValueError("FIFO layer quantity cannot be negative.")
    qty = 0.0 if abs(qty) <= QTY_TOLERANCE else qty

    if 'total_value' in layer and layer.get('total_value') is not None:
        total_value = _finite_number(layer['total_value'], 'Layer total value')
    else:
        supplied_rate = _finite_number(layer.get('unit_cost', 0), 'Layer unit cost')
        total_value = qty * supplied_rate
    if total_value < -VALUE_TOLERANCE:
        raise ValueError("FIFO layer total value cannot be negative.")
    total_value = 0.0 if abs(total_value) < 1e-12 else total_value
    unit_cost = total_value / qty if qty > QTY_TOLERANCE else 0.0

    normalized = copy.deepcopy(layer)
    normalized.update({'qty': qty, 'total_value': total_value, 'unit_cost': unit_cost})
    return normalized

def _layer_control_totals(layer_map):
    qty = value = 0.0
    layer_count = 0
    for layers in layer_map.values():
        for raw in layers:
            layer = _canonicalize_layer(raw)
            qty += layer['qty']
            value += layer['total_value']
            layer_count += 1
    return {'layer_count': layer_count, 'quantity': qty, 'value': value}

class FIFOLayerStore:
    def __init__(self, products):
        self.products = products
        if 'inventory_layers' not in st.session_state:
            st.session_state['inventory_layers'] = {pid: [] for pid in products}
        if 'fifo_period_history' not in st.session_state:
            st.session_state['fifo_period_history'] = []
        if 'fifo_last_closed_period' not in st.session_state:
            st.session_state['fifo_last_closed_period'] = None
        if 'fifo_value_variance' not in st.session_state:
            st.session_state['fifo_value_variance'] = {pid: 0.0 for pid in products}

    @property
    def _store(self):
        return st.session_state['inventory_layers']

    def get(self, alias):
        return copy.deepcopy(self._store.get(alias, []))

    def set(self, alias, layers):
        self._store[alias] = copy.deepcopy(layers)

    def all_layers(self):
        return copy.deepcopy(self._store)

    def current_qty(self, alias):
        return sum(l['qty'] for l in self.get(alias))

    def sync_beginning(self, alias, expected_beg_qty):
        layers = self.get(alias)
        if not layers and expected_beg_qty > 0:
            layers = [{
                'date': 'Prior Period Carryover',
                'qty': expected_beg_qty,
                'unit_cost': 0.0,
                'total_value': 0.0
            }]
        return layers

    def variance_for(self, alias, expected_beg_qty):
        layers = self.get(alias)
        actual_qty = sum(l['qty'] for l in layers)
        actual_val = sum(l.get('total_value', l['qty'] * l['unit_cost']) for l in layers)
        variance_qty = actual_qty - expected_beg_qty
        avg_cost = (actual_val / actual_qty) if actual_qty else 0.0
        variance_val = variance_qty * avg_cost
        return variance_qty, variance_val

    def seed_opening_layers(self, seed_dict):
        overwritten = [alias for alias, layers in seed_dict.items() if self.get(alias)]
        for alias, layers in seed_dict.items():
            self.set(alias, [_canonicalize_layer(l) for l in layers])
        return overwritten

    # ------------------------------------------------------------------
    # Known beginning-value-variance
    # ------------------------------------------------------------------
    # This is the gap between a product's beginning value as stated on the
    # current period's Master Grid upload (the user's own spreadsheet total
    # for that point in time) and what the FIFO layers actually carry forward
    # as their beginning value. It is computed fresh from the upload every
    # period by fifo_calculations.stage_batch_calculation and written here via
    # set_value_variance — nothing here seeds or maintains a standing figure.
    # It is expected to be nonzero (the user's legacy spreadsheet computes an
    # "Ending Inventory" waterfall and a "TOTAL" layer-sum independently and
    # they don't always agree to the penny) but should not drift outside the
    # normal accepted range (see VALUE_VARIANCE_DRIFT_ACCEPT_MIN/MAX in
    # excel_export.py) from what was computed and accepted as of the prior
    # period's close — that drift check is what last_committed_value_variance
    # below supports.

    def get_value_variance(self, alias):
        return float(st.session_state.get('fifo_value_variance', {}).get(alias, 0.0))

    def set_value_variance(self, alias, value):
        value = _finite_number(value, f"Value variance for alias {alias}")
        variances = dict(st.session_state.get('fifo_value_variance', {}))
        variances[alias] = value
        st.session_state['fifo_value_variance'] = variances

    def all_value_variances(self):
        return {pid: float(st.session_state.get('fifo_value_variance', {}).get(pid, 0.0)) for pid in self.products}

    def last_committed_value_variance(self, alias):
        """The value variance accepted as of the most recent closed period that
        recorded one for this alias. Falls back to the current working value
        when no committed period yet carries this data — e.g. before the very
        first period is closed under this control, or for history recorded
        before this feature existed — which yields a variance drift of $0.00
        the first time a period is evaluated, rather than a false alarm."""
        for record in reversed(st.session_state.get('fifo_period_history', [])):
            vv = record.get('value_variance')
            if vv is not None and alias in vv:
                return float(vv[alias])
        return self.get_value_variance(alias)

    def reset_all(self):
        st.session_state['inventory_layers'] = {pid: [] for pid in self.products}
        st.session_state['fifo_period_history'] = []
        st.session_state['fifo_last_closed_period'] = None
        st.session_state['fifo_value_variance'] = {pid: 0.0 for pid in self.products}
        st.session_state.pop('staged_fifo_run', None)
        st.session_state.pop('last_fifo_report', None)

    def to_json(self):
        payload = {
            'schema_version': SNAPSHOT_SCHEMA_VERSION,
            'engine_version': ENGINE_VERSION,
            'created_at_utc': datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds') + 'Z',
            'last_closed_period': st.session_state.get('fifo_last_closed_period'),
            'period_history': st.session_state.get('fifo_period_history', []),
            'product_aliases': sorted(self.products),
            'inventory_layers': self.all_layers(),
            'value_variance': self.all_value_variances(),
        }
        payload['control_totals'] = _layer_control_totals(payload['inventory_layers'])
        return json.dumps(payload, indent=2, default=str)

    def from_json(self, json_str):
        data = json.loads(json_str)
        if 'inventory_layers' in data:
            schema_version = data.get('schema_version', 1)
            if schema_version > SNAPSHOT_SCHEMA_VERSION:
                raise ValueError(
                    f"Snapshot schema {schema_version} is newer than this app supports "
                    f"({SNAPSHOT_SCHEMA_VERSION})."
                )
            raw_layers = data['inventory_layers']
            history = data.get('period_history', [])
            last_closed = data.get('last_closed_period')
            raw_value_variance = data.get('value_variance', {})
        else:
            raw_layers = data
            history = []
            last_closed = None
            raw_value_variance = {}

        if not isinstance(raw_layers, dict):
            raise ValueError("Snapshot inventory_layers must be a JSON object keyed by product alias.")

        validated = {pid: [] for pid in self.products}
        unknown_aliases = []
        for raw_alias, layers in raw_layers.items():
            try:
                alias = int(raw_alias)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid product alias in snapshot: {raw_alias!r}") from exc
            if alias not in self.products:
                unknown_aliases.append(alias)
                continue
            if not isinstance(layers, list):
                raise ValueError(f"Snapshot layers for alias {alias} must be a list.")
            validated[alias] = [_canonicalize_layer(layer) for layer in layers]

        if unknown_aliases:
            raise ValueError(f"Snapshot contains unknown product aliases: {sorted(unknown_aliases)}")
        if not isinstance(history, list):
            raise ValueError("Snapshot period_history must be a list.")

        if not isinstance(raw_value_variance, dict):
            raise ValueError("Snapshot value_variance must be a JSON object keyed by product alias.")
        validated_value_variance = {pid: 0.0 for pid in self.products}
        for raw_alias, value in raw_value_variance.items():
            try:
                alias = int(raw_alias)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid product alias in snapshot value_variance: {raw_alias!r}") from exc
            if alias in self.products:
                validated_value_variance[alias] = _finite_number(value, f"Snapshot value variance for alias {alias}")

        normalized_history = []
        for record in history:
            record = copy.deepcopy(record)
            for key in ('beginning_layers', 'ending_layers', 'value_variance_before',
                        'value_variance', 'product_period_summary'):
                if key in record and isinstance(record[key], dict):
                    record[key] = {int(k): v for k, v in record[key].items()}
            record.setdefault('product_period_summary', {})
            normalized_history.append(record)
        history = normalized_history

        expected_totals = data.get('control_totals') if 'inventory_layers' in data else None
        if expected_totals:
            actual_totals = _layer_control_totals(validated)
            if (int(expected_totals.get('layer_count', -1)) != actual_totals['layer_count']
                    or abs(_finite_number(expected_totals.get('quantity'), 'Snapshot control quantity')
                           - actual_totals['quantity']) > QTY_TOLERANCE
                    or abs(_finite_number(expected_totals.get('value'), 'Snapshot control value')
                           - actual_totals['value']) >= VALUE_TOLERANCE):
                raise ValueError("Snapshot control totals do not agree with its FIFO layers.")

        st.session_state['inventory_layers'] = validated
        st.session_state['fifo_period_history'] = history
        st.session_state['fifo_last_closed_period'] = last_closed
        st.session_state['fifo_value_variance'] = validated_value_variance
        st.session_state.pop('staged_fifo_run', None)
        st.session_state.pop('last_fifo_report', None)

    # ------------------------------------------------------------------
    # Disk-backed durability
    # ------------------------------------------------------------------
    # Session state alone does not survive a server restart, redeploy, or a
    # sleeping/waking host. These methods make every committed period write
    # a real file to disk automatically, and let a fresh session recover the
    # latest one on startup, so "close period" — not "remember to click
    # download" — is what actually protects the data. Each committed period
    # gets its own file (the payload is a *full* snapshot including
    # period_history back to the start, not just that period's delta), so
    # the most-recently-modified file on disk is always sufficient to fully
    # recover, and older files remain as a rollback trail beyond what
    # reopen_latest alone covers.

    def _snapshot_path(self, period_key):
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        safe_key = period_key.replace("/", "-")
        return SNAPSHOT_DIR / f"{safe_key}.json"

    def _write_snapshot_to_disk(self, period_key):
        """Writes the current state to disk atomically and verifies the
        write by reading it back and re-checking control totals. Raises if
        the write or verification fails — a period that reports itself as
        closed should not silently lack a durable backup."""
        payload = self.to_json()
        path = self._snapshot_path(period_key)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(path)  # atomic on POSIX/NTFS: avoids a half-written file on crash

        written = json.loads(path.read_text(encoding="utf-8"))
        expected_totals = written.get("control_totals", {})
        actual_totals = _layer_control_totals(self.all_layers())
        if (int(expected_totals.get("layer_count", -1)) != actual_totals["layer_count"]
                or abs(expected_totals.get("quantity", 0) - actual_totals["quantity"]) > QTY_TOLERANCE
                or abs(expected_totals.get("value", 0) - actual_totals["value"]) >= VALUE_TOLERANCE):
            raise IOError(f"Snapshot written to {path} failed its read-back verification.")
        return path

    @staticmethod
    def _latest_snapshot_path():
        if not SNAPSHOT_DIR.exists():
            return None
        files = sorted(SNAPSHOT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        return files[-1] if files else None

    def load_latest_snapshot_from_disk(self):
        """Loads the most recently written snapshot file, if any. Returns the
        loaded period_key on success, or None if there was nothing on disk to
        load. Raises if a snapshot file exists but fails validation — that
        should surface to the user rather than silently falling back to a
        blank app, since a blank app would look identical to "there was
        genuinely nothing to restore" from the user's point of view."""
        path = self._latest_snapshot_path()
        if path is None:
            return None
        self.from_json(path.read_text(encoding="utf-8"))
        return path.stem

    @staticmethod
    def list_snapshot_files():
        """All snapshot files on disk, most recent first. Useful for a manual
        'restore from a specific period' picker if the latest file isn't what
        someone wants (e.g. recovering from a bad close by loading an older one)."""
        if not SNAPSHOT_DIR.exists():
            return []
        return sorted(SNAPSHOT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    def period_history(self):
        """The full list of closed-period history records, oldest first."""
        return copy.deepcopy(st.session_state.get('fifo_period_history', []))

    def product_period_history(self, alias):
        """Every closed period's roll-forward figures for one product alias,
        oldest first, drawn from each record's `product_period_summary`
        (see commit_period). Periods closed before that field existed, or
        where this particular alias had no entry recorded, are simply
        omitted — callers should treat a shorter list than the full period
        history as normal, not as missing/broken data."""
        rows = []
        for record in st.session_state.get('fifo_period_history', []):
            summary = record.get('product_period_summary', {}).get(alias)
            if summary is None:
                continue
            row = dict(summary)
            row['period_key'] = record.get('period_key')
            row['as_of_date'] = record.get('as_of_date')
            rows.append(row)
        return rows

    @staticmethod
    def period_key(fiscal_year, period):
        return f"FY{int(fiscal_year)}-P{int(period):02d}"

    @staticmethod
    def next_period(fiscal_year, period):
        return (int(fiscal_year) + 1, 1) if int(period) == 13 else (int(fiscal_year), int(period) + 1)

    def period_is_closed(self, fiscal_year, period):
        key = self.period_key(fiscal_year, period)
        return any(item.get('period_key') == key for item in st.session_state.get('fifo_period_history', []))

    def sequence_error(self, fiscal_year, period):
        last = st.session_state.get('fifo_last_closed_period')
        if not last:
            return None
        expected_year, expected_period = self.next_period(last['fiscal_year'], last['period'])
        if (int(fiscal_year), int(period)) != (expected_year, expected_period):
            expected_key = self.period_key(expected_year, expected_period)
            return f"The next allowable period is {expected_key}; restore or reopen the latest period before processing another period."
        return None

    def commit_period(self, fiscal_year, period, staged_layers, run_signature, control_counts, as_of_date,
                       product_period_summary=None):
        """`product_period_summary`, if provided, is a per-alias dict of this
        period's roll-forward figures (beginning/purchases/usage/ending qty
        and value) as computed by the staged preview at commit time. It is
        stored on the history record so cross-period reporting — turnover,
        days-of-supply, trend charts — can read historical figures directly
        from fifo_period_history instead of needing a fresh preview run for
        every period being reported on. It is optional and additive: older
        history records (closed before this field existed) simply won't
        have it, and callers should treat its absence as "not available for
        this period" rather than an error."""
        if self.period_is_closed(fiscal_year, period):
            raise ValueError(f"{self.period_key(fiscal_year, period)} has already been closed.")
        sequence_error = self.sequence_error(fiscal_year, period)
        if sequence_error:
            raise ValueError(sequence_error)

        before = self.all_layers()
        after = {pid: copy.deepcopy(staged_layers.get(pid, [])) for pid in self.products}
        # Mirror beginning_layers/ending_layers for the value-variance baseline: record
        # both what was in effect when this period STARTED (so reopening can restore it,
        # exactly like beginning_layers does for the layers themselves, unconditionally —
        # not dependent on an earlier history record existing) and what was accepted as of
        # this period's close (the new baseline the NEXT period's drift check compares
        # against).
        value_variance_before = {pid: self.last_committed_value_variance(pid) for pid in self.products}
        record = {
            'period_key': self.period_key(fiscal_year, period),
            'fiscal_year': int(fiscal_year),
            'period': int(period),
            'as_of_date': as_of_date.isoformat(),
            'committed_at_utc': datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds') + 'Z',
            'engine_version': ENGINE_VERSION,
            'run_signature': run_signature,
            'control_counts': control_counts,
            'beginning_layers': before,
            'ending_layers': after,
            'ending_control_totals': _layer_control_totals(after),
            'value_variance_before': value_variance_before,
            'value_variance': self.all_value_variances(),
            'product_period_summary': copy.deepcopy(product_period_summary) if product_period_summary else {},
        }

        st.session_state['inventory_layers'] = after
        history = copy.deepcopy(st.session_state.get('fifo_period_history', []))
        history.append(record)
        st.session_state['fifo_period_history'] = history
        st.session_state['fifo_last_closed_period'] = {
            'fiscal_year': int(fiscal_year), 'period': int(period),
            'period_key': record['period_key'], 'run_signature': run_signature,
        }

        # Durability: every committed period is written to disk immediately,
        # verified by read-back, before commit_period returns. If this raises,
        # the in-memory commit above has already happened — the caller (app.py)
        # surfaces the exception so the user knows the close succeeded in this
        # session but was NOT safely backed up, rather than believing both
        # succeeded when only one did.
        self._write_snapshot_to_disk(record['period_key'])

    def can_reopen_latest(self, fiscal_year, period):
        history = st.session_state.get('fifo_period_history', [])
        return bool(history and history[-1].get('period_key') == self.period_key(fiscal_year, period))

    def reopen_latest(self, fiscal_year, period):
        if not self.can_reopen_latest(fiscal_year, period):
            raise ValueError("Only the latest closed period can be reopened safely.")
        history = copy.deepcopy(st.session_state.get('fifo_period_history', []))
        record = history.pop()
        restored = {int(k): copy.deepcopy(v) for k, v in record['beginning_layers'].items()}
        st.session_state['inventory_layers'] = {pid: restored.get(pid, []) for pid in self.products}
        st.session_state['fifo_period_history'] = history

        # Restore the value-variance baseline exactly like the layers above: from the
        # popped record's own "before" snapshot, unconditionally — this works even when
        # reopening the very first-ever committed period, where there is no earlier
        # history record left to fall back to.
        value_variance_before = record.get('value_variance_before')
        if value_variance_before is not None:
            st.session_state['fifo_value_variance'] = {
                pid: float(value_variance_before.get(pid, 0.0)) for pid in self.products
            }

        if history:
            previous = history[-1]
            st.session_state['fifo_last_closed_period'] = {
                'fiscal_year': previous['fiscal_year'], 'period': previous['period'],
                'period_key': previous['period_key'], 'run_signature': previous['run_signature'],
            }
        else:
            st.session_state['fifo_last_closed_period'] = None
        st.session_state.pop('staged_fifo_run', None)

        # Durability: a reopen changes what "current state" means just as much
        # as a commit does, so write it through to disk too. Reopening without
        # this would leave the on-disk snapshot pointing at the period that was
        # just reopened, so a session loss right after reopening would silently
        # restore the wrong (undone) state instead of what reopen_latest left in
        # memory.
        new_key = (
            self.period_key(st.session_state['fifo_last_closed_period']['fiscal_year'],
                             st.session_state['fifo_last_closed_period']['period'])
            if st.session_state['fifo_last_closed_period']
            else f"{self.period_key(fiscal_year, period)}-REOPENED"
        )
        self._write_snapshot_to_disk(new_key)