"""Golden-file tests for FIFO: today's numbers, frozen, so a refactor cannot move one silently.

The scenario is synthetic (made-up layers and receipts) but exercises consolidation, a price
break, spanning depletion, a zero-balance product, and an under-count that FAILs. If a test here
fails, the output changed. Decide whether that was intended (see CLAUDE.md: never change
accounting results silently, and bump ``ENGINE_VERSION`` for a calculation change); only then
regenerate with ``GOLDEN_UPDATE=1`` and review the git diff.
"""
from pathlib import Path

import pandas as pd

from apps.fifo_inventory.excel_export import _compute_product_controls, _compute_summary_control_status
from apps.fifo_inventory.fifo_calculations import stage_batch_calculation, summarize_control_counts
from shared.golden import assert_matches_golden

from .conftest import AS_OF, layer, receipts

GOLDEN = Path(__file__).resolve().parent / "golden"

PRODUCTS = {2: "Preforms", 3: "Caps", 4: "Labels", 5: "Film"}


def _scenario(store):
    store.set(2, [layer("2026-05-01", 1_000_000, 25_000.00), layer("2026-06-10", 500_000, 13_250.00)])
    store.set(3, [layer("2026-06-01", 200_000, 1_500.00)])
    store.set(4, [layer("2026-06-15", 40_000, 400.00)])
    store.set(5, [layer("2026-06-20", 10_000, 5_000.00)])
    grid = pd.DataFrame({
        "PRODUCT ALIAS": [2, 3, 4, 5],
        "02": [1_500_000.0, 200_000.0, 40_000.0, 10_000.0],       # beginning (period 2)
        "03": [1_100_000.0, 0.0, 40_000.0, 12_000.0],              # ending   (period 3)
    })
    rec = receipts(
        ("2026-07-01", 300_000, 8_100.00, "PO-10", "INV-1", 2),
        ("2026-07-02", 300_000, 8_100.00, "PO-11", "INV-2", 2),    # same unit cost: consolidates
        ("2026-07-03", 100_000, 3_100.00, "PO-12", "INV-3", 2),    # price break: its own layer
        ("2026-07-04", 50_000, 400.00, "PO-20", "INV-4", 3),
    )
    return grid, rec


def test_staged_batch_results(make_store):
    store = make_store(PRODUCTS)
    grid, rec = _scenario(store)
    results, staged, errors = stage_batch_calculation(PRODUCTS, grid, rec, store, 2026, 3, AS_OF)

    assert_matches_golden(GOLDEN, "staged_batch", {
        "errors": errors,
        "results": [{k: r[k] for k in ("alias", "metrics", "on_hand", "depleted", "receivings",
                                       "beg_variance_qty", "beg_variance_val")} for r in results],
        "staged_layers": staged,
    })


def test_controls_for_the_batch(make_store):
    store = make_store(PRODUCTS)
    grid, rec = _scenario(store)
    results, _staged, _errors = stage_batch_calculation(PRODUCTS, grid, rec, store, 2026, 3, AS_OF)

    assert_matches_golden(GOLDEN, "batch_controls", {
        "counts": summarize_control_counts(results),
        "per_product": {
            str(r["alias"]): {
                "summary": _compute_summary_control_status(r),
                "checks": _compute_product_controls(r),
            } for r in results
        },
    })
