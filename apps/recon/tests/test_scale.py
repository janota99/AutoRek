"""The scale the landing page quotes: a typical month is about 700 QuickBooks lines against 2,500 Infinium lines.

Synthetic rows of that size go through the real matching engine. The bound is deliberately generous (the page says
"about a second"); this exists so that claim cannot quietly become false.
"""
import time

import pandas as pd

from apps.recon.matching.reconciliation import build_reconciliation


def test_a_typical_month_matches_quickly(make_metadata):
    qb_n, inf_n = 700, 2500
    inf = pd.DataFrame({
        "PO": [str(50000 + i) for i in range(inf_n)], "Invoice": [f"INV{70000 + i}" for i in range(inf_n)],
        "Amount": [round(50 + (i * 37) % 24000 + (i % 100) / 100, 2) for i in range(inf_n)],
        "Period": 12, "Customer": [f"Customer {i % 90}" for i in range(inf_n)], "Date": "2026-09-15",
    })
    qb = inf.iloc[:qb_n].copy()
    qb["Qty"] = 1
    qb.loc[qb.index[::40], "Amount"] += 27.0       # about 2.5% amount differences stay open for review
    qb_map = {"po": "PO", "invoice": "Invoice", "amount": "Amount", "quantity": "Qty", "period": "Period",
              "product": None, "customer": "Customer", "date": "Date"}
    inf_map = {"po": "PO", "invoice": "Invoice", "amount": "Amount", "period": "Period",
               "customer": "Customer", "date": "Date"}
    started = time.perf_counter()
    result = build_reconciliation(qb, inf, qb_map, inf_map, make_metadata(fiscal_period=12), 2026)
    elapsed = time.perf_counter() - started
    assert result is not None
    assert elapsed < 15, f"{qb_n} vs {inf_n} rows took {elapsed:.1f}s"
