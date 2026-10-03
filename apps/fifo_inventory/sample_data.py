"""Demo uploads for the FIFO page, built from the layers currently in the app.

A fixed file could not reconcile: the Master Grid's beginning quantity and value must equal the
layers actually carried, and those depend on the selected period and what has been closed. So
the sample is generated on demand, as a plan per product: a small receipt for some products, a
usage of about a third of the stock, and the ending quantity and value that strict
oldest-first consumption gives. Both files come from the same plan, so they always agree.

Nothing is written to disk and no layer is changed. The page blocks Close & Commit while a
sample is loaded, so demo numbers can never become the official books.
"""

from __future__ import annotations

import io
from datetime import date, timedelta

from openpyxl import Workbook

from .fifo_layer_store import _canonicalize_layer
from .user_inputs import PRODUCTS

_RECEIPT_EVERY = 4          # every 4th product (by alias order) gets a receipt
_USAGE_SHARE = 0.35         # share of (beginning + receipts) consumed in the period
_FALLBACK_RECEIPT_QTY = 100000
_FALLBACK_UNIT_COST = 0.01

RECEIPT_HEADERS = ['PRODUCT ALIAS', 'DESCRIPTION', 'ITEMS', 'COMPANY', 'DATE DELIVERED',
                   'LOT #', 'BOL #', 'SEAL #', 'PO #', 'QUANTITY', 'PRICE', 'PERIOD', 'INVOICE #']


def sample_token(fiscal_year: int, period: int, as_of: date, layer_store) -> str:
    """Names everything a sample depends on, so it is rebuilt when any of it changes."""
    totals = sum(_canonicalize_layer(l)['qty'] for a in PRODUCTS for l in layer_store.get(a))
    return f"{fiscal_year}-{period}-{as_of.isoformat()}-{totals:.4f}"


def _consume(layers, qty):
    """Strict oldest-first consumption; returns the layers left."""
    left = qty
    remaining = []
    for layer in layers:
        if left <= 0:
            remaining.append(layer)
            continue
        take = min(layer['qty'], left)
        left -= take
        kept = layer['qty'] - take
        if kept > 0:
            remaining.append({'qty': kept, 'total_value': layer['total_value'] * kept / layer['qty']})
    return remaining


def build_plan(layer_store, as_of: date) -> dict:
    plan = {}
    for index, alias in enumerate(sorted(PRODUCTS)):
        layers = [_canonicalize_layer(l) for l in layer_store.get(alias)]
        begin_qty = sum(l['qty'] for l in layers)
        begin_val = sum(l['total_value'] for l in layers)
        receipt = None
        if index % _RECEIPT_EVERY == 0:
            if begin_qty > 0:
                qty = max(1000, round(begin_qty * 0.15, -3))
                unit = layers[-1]['unit_cost'] * 1.03
            else:
                qty, unit = _FALLBACK_RECEIPT_QTY, _FALLBACK_UNIT_COST
            receipt = {'qty': float(qty), 'value': round(qty * unit, 2),
                       'date': as_of - timedelta(days=3 + index % 5)}
        stock = layers + ([{'qty': receipt['qty'], 'total_value': receipt['value']}] if receipt else [])
        total_qty = begin_qty + (receipt['qty'] if receipt else 0.0)
        usage = float(int(total_qty * _USAGE_SHARE))
        left = _consume(stock, usage)
        plan[alias] = {
            'begin_qty': begin_qty, 'begin_val': round(begin_val, 2), 'receipt': receipt,
            'end_qty': total_qty - usage, 'end_val': round(sum(l['total_value'] for l in left), 2),
        }
    return plan


def build_master_grid_sample(layer_store, fiscal_year: int, period: int, as_of: date) -> tuple[str, bytes]:
    plan = build_plan(layer_store, as_of)
    begin_period = 13 if period == 1 else period - 1
    wb = Workbook()
    ws = wb.active
    ws.title = "Master Grid"
    headers = ['PRODUCT ALIAS', 'DESCRIPTION']
    for p in range(1, 14):
        headers += [f"{p:02d}", f"{p:02d}V"]
    ws.append(headers)
    for alias, name in sorted(PRODUCTS.items()):
        row = [alias, name]
        for p in range(1, 14):
            if p == begin_period:
                row += [plan[alias]['begin_qty'], plan[alias]['begin_val']]
            elif p == period:
                row += [plan[alias]['end_qty'], plan[alias]['end_val']]
            else:
                row += [0.0, 0.0]
        ws.append(row)
    ws.freeze_panes = "C2"
    out = io.BytesIO()
    wb.save(out)
    return f"fifo_master_grid_P{period:02d}.xlsx", out.getvalue()


def build_receipts_sample(layer_store, fiscal_year: int, period: int, as_of: date) -> tuple[str, bytes]:
    plan = build_plan(layer_store, as_of)
    wb = Workbook()
    ws = wb.active
    ws.title = "Receipts"
    ws.append(RECEIPT_HEADERS)
    number = 0
    for alias, name in sorted(PRODUCTS.items()):
        receipt = plan[alias]['receipt']
        if not receipt:
            continue
        number += 1
        ws.append([
            alias, name, "Sample item", "Sample Supplier Co.", receipt['date'],
            f"L-SAMPLE-{number:03d}", f"BOL-{number:04d}", f"SEAL-{number:03d}", f"PO-S{number:04d}",
            receipt['qty'], receipt['value'], period, f"INV-S{number:04d}",
        ])
    for row in ws.iter_rows(min_row=2, min_col=5, max_col=5):
        row[0].number_format = 'yyyy-mm-dd'
    out = io.BytesIO()
    wb.save(out)
    return f"fifo_receipts_P{period:02d}.xlsx", out.getvalue()
