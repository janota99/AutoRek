"""Shared fixtures for the FIFO tests.

``FIFOLayerStore`` keeps its state in ``st.session_state`` and writes snapshots to disk. These
fixtures swap in a plain dict and a scratch folder, so no test touches a real Streamlit session,
``fifo_snapshots/``, or ``app_settings.json``.
"""
from datetime import date

import pandas as pd
import pytest

from apps.fifo_inventory import app_settings, fifo_layer_store

AS_OF = date(2026, 8, 31)  # fixed so layer ages never depend on today's date


class SessionState(dict):
    """Stand-in for st.session_state: a dict with attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


@pytest.fixture
def session_state(monkeypatch):
    state = SessionState()
    monkeypatch.setattr(fifo_layer_store.st, "session_state", state)
    return state


@pytest.fixture
def snapshot_dir(monkeypatch, tmp_path):
    folder = tmp_path / "fifo_snapshots"
    monkeypatch.setattr(fifo_layer_store, "SNAPSHOT_DIR", folder)
    return folder


@pytest.fixture
def settings_path(monkeypatch, tmp_path):
    path = tmp_path / "app_settings.json"
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", path)
    return path


@pytest.fixture
def make_store(session_state, snapshot_dir):
    """Factory: ``make_store({2: 'A', 3: 'B'})`` -> an empty FIFOLayerStore over those products."""
    def _make(products=None):
        return fifo_layer_store.FIFOLayerStore(products or {2: "Product A", 3: "Product B"})
    return _make


def receipts(*rows):
    """Receipts frame from ``(date, qty, total_value[, po, invoice, alias])`` tuples."""
    records = []
    for row in rows:
        day, qty, value, *rest = row
        record = {"DATE DELIVERED": day, "QUANTITY": qty, "PRICE": value,
                  "PO #": rest[0] if len(rest) > 0 else "", "INVOICE #": rest[1] if len(rest) > 1 else "",
                  "ITEMS": "ITEM"}
        if len(rest) > 2:
            record["PRODUCT ALIAS"] = rest[2]
        records.append(record)
    return pd.DataFrame(records)


def layer(day, qty, total_value, **extra):
    return {"date": day, "qty": qty, "unit_cost": total_value / qty if qty else 0.0,
            "total_value": total_value, **extra}
