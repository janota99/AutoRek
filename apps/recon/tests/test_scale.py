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


def test_messy_references_do_not_make_matching_quadratic(make_metadata):
    """Generic and shared POs, typos and repeated amounts push many rows through the fuzzy and typo passes.
    Those passes once compared every leftover QuickBooks row with every leftover Infinium row cell by cell;
    3x a typical month took about 4 seconds then and well under 2 now."""
    import random
    rng = random.Random(1)
    qb_n, inf_n = 2100, 7500
    pos = [rng.choice(["HOPPER", "LOWES", "STOCK", "TRUCK"]) if (r := rng.random()) < .10
           else str(50000 + i // 3) if r < .40 else str(50000 + i) for i in range(inf_n)]
    inf = pd.DataFrame({
        "PO": pos, "Invoice": [f"INV{70000 + i}" if rng.random() > .3 else "" for i in range(inf_n)],
        "Amount": [round(rng.choice([3505.70, 1200.00, 845.25]) if rng.random() < .2 else 50 + rng.random() * 24000, 2)
                   for _ in range(inf_n)],
        "Period": 12, "Customer": [f"Customer {i % 90}" for i in range(inf_n)], "Date": "2026-09-15",
    })
    qb = inf.sample(qb_n, random_state=2).reset_index(drop=True)
    qb["Qty"] = 1
    typo = qb.sample(frac=.1, random_state=3).index
    qb.loc[typo, "PO"] = qb.loc[typo, "PO"].str[:-1] + "X"
    qb_map = {"po": "PO", "invoice": "Invoice", "amount": "Amount", "quantity": "Qty", "period": "Period",
              "product": None, "customer": "Customer", "date": "Date"}
    inf_map = {k: v for k, v in qb_map.items() if k not in ("quantity", "product")}
    started = time.perf_counter()
    assert build_reconciliation(qb, inf, qb_map, inf_map, make_metadata(fiscal_period=12), 2026) is not None
    elapsed = time.perf_counter() - started
    assert elapsed < 10, f"{qb_n} vs {inf_n} messy rows took {elapsed:.1f}s"
