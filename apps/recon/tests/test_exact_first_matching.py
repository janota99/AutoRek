"""Matching policy 2026.10-EXACT-FIRST-NO-ORDER-PAIRING.

1. Text with punctuation kept first. After the documented normalizations (whitespace, case, a leading "PO" prefix on
   a PO, a trailing ".0"), rows match on the identifier and the exact signed amount in cents, and only when that
   identifies a unique one-to-one pair. This is prefix normalization, not literal character-for-character equality;
   punctuation INSIDE the identifier is never ignored in this first step.
2. Punctuation-insensitive matching is a fallback for rows still unmatched. It may pair only the single remaining
   candidate on each side, and the match is labelled ACCEPTED_MATCH_WITH_IDENTIFIER_DISCREPANCY. With several
   candidates the rows are held for review; nothing is chosen by source order.
3. "Specific" is not "unique". Repeated no-digit references with the same amount (LOWES) are resolved only by a
   defined field such as an invoice number; dates are not a matching key; otherwise the group is held.

Every scenario is run with both file orders, because no result may depend on the order of either file. Held rows
must stay out of journal-entry support and every row and dollar must still be accounted for. Synthetic data only.
"""

from __future__ import annotations

import itertools

import pandas as pd
import pytest

from apps.recon.matching import build_reconciliation
from apps.recon.matching.labels import (
    CLASS_ACCEPTED_MATCH,
    CLASS_ACCEPTED_MATCH_ID_DISCREPANCY,
    DISPOSITION_MATCHED,
    DISPOSITION_REVIEW_HOLD,
    DISPOSITION_TRUE_UNMATCHED,
)


def _qb(po, invoice, amount, customer="Acme", date="2026-01-05"):
    return {"PO": po, "Invoice": invoice, "Amount": amount, "Qty": 1, "Period": "1",
            "Customer": customer, "Date": date, "Memo": ""}


def _inf(po, invoice, amount, customer="Acme", date="2026-01-05"):
    return {"PO": po, "Invoice": invoice, "Amount": amount, "Period": "1", "Customer": customer, "Date": date}


def _run(qb_rows, inf_rows, qb_mapping, inf_mapping, make_metadata):
    return build_reconciliation(
        pd.DataFrame(qb_rows), pd.DataFrame(inf_rows), qb_mapping, inf_mapping, make_metadata(), 2026,
    )


def _ledger(result):
    return result.qb_dispositions.set_index("QBO Row ID")


def _cents(value) -> int:
    return int(round(float(value) * 100))


def _pairs(result, qb_rows, inf_rows):
    """The accepted one-to-one matches as (QuickBooks row dict, Infinium row dict), whatever the file order."""
    return [(qb_rows[g.qb_rows[0]], inf_rows[g.inf_rows[0]]) for g in result.matches]


def _assert_integrity(result, qb_rows):
    """No row is used twice, every row and dollar is accounted for, and held rows stay out of JE support."""
    used_inf = [index for group in result.matches for index in group.inf_rows]
    used_qb = [index for group in result.matches for index in group.qb_rows]
    assert len(used_inf) == len(set(used_inf)), "an Infinium row was matched twice"
    assert len(used_qb) == len(set(used_qb)), "a QuickBooks row was matched twice"

    ledger = result.qb_dispositions
    assert len(ledger) == len(qb_rows) and ledger["QBO Row ID"].is_unique       # every row, exactly once
    assert result.metrics["Control Status"] == "PASS"
    assert (result.controls["Status"] == "PASS").all()

    by_disposition = {name: sum(_cents(a) for a in group["Amount"]) for name, group in ledger.groupby("Final Disposition")}
    assert sum(by_disposition.values()) == sum(_cents(row["Amount"]) for row in qb_rows)    # every dollar, to the cent

    held = ledger[ledger["Final Disposition"] == DISPOSITION_REVIEW_HOLD]
    assert (held["In Proposed JE"] == "No").all()
    true_unmatched = ledger[ledger["Final Disposition"] == DISPOSITION_TRUE_UNMATCHED]
    assert _cents(result.metrics["Proposed JE Amount"]) == sum(_cents(a) for a in true_unmatched["Amount"])


def _both_orders(qb_rows, inf_rows):
    """Every combination of file order: QuickBooks forward/reversed x Infinium forward/reversed."""
    for qb_order, inf_order in itertools.product((False, True), repeat=2):
        yield (list(reversed(qb_rows)) if qb_order else list(qb_rows),
               list(reversed(inf_rows)) if inf_order else list(inf_rows))


# ---------------------------------------------------------------------------
# 1. Text with punctuation kept first
# ---------------------------------------------------------------------------

def test_hp_2231_and_hp2231_are_paired_by_exact_text_in_every_file_order(qb_mapping, inf_mapping, make_metadata):
    qb_rows = [_qb("HP-2231", "", 4860.00, date="2026-01-05"), _qb("HP2231", "", 4860.00, date="2026-01-19")]
    inf_rows = [_inf("HP-2231", "", 4860.00, date="2026-01-05"), _inf("HP2231", "", 4860.00, date="2026-01-19")]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        pairs = _pairs(result, qb_order, inf_order)
        assert len(pairs) == 2
        assert all(q["PO"] == i["PO"] for q, i in pairs), "a row was paired with the other punctuation variant"
        assert {q["PO"] for q, _ in pairs} == {"HP-2231", "HP2231"}
        assert set(_ledger(result)["Classification Code"]) == {CLASS_ACCEPTED_MATCH}   # exact: no discrepancy label
        assert all(g.method == "PO + Amount" and not g.identifier_discrepancy for g in result.matches)
        _assert_integrity(result, qb_order)


def test_exact_invoice_text_decides_before_punctuation_is_ignored(qb_mapping, inf_mapping, make_metadata):
    qb_rows = [_qb("", "INV-77", 500.00), _qb("", "INV77", 500.00)]
    inf_rows = [_inf("", "INV-77", 500.00), _inf("", "INV77", 500.00)]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        pairs = _pairs(result, qb_order, inf_order)
        assert len(pairs) == 2 and all(q["Invoice"] == i["Invoice"] for q, i in pairs)
        assert all(g.method == "Invoice + Amount" for g in result.matches)
        _assert_integrity(result, qb_order)


def test_the_exact_match_wins_even_when_a_punctuation_variant_is_also_present(qb_mapping, inf_mapping, make_metadata):
    # One QuickBooks row; Infinium has the exact text and a punctuation variant. The exact text is the unique
    # exact candidate, so it is the match; the variant is simply left over (an Infinium-only exception).
    qb_rows = [_qb("AB-12", "", 80.00)]
    inf_rows = [_inf("AB12", "", 80.00), _inf("AB-12", "", 80.00)]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        (pair,) = _pairs(result, qb_order, inf_order)
        assert pair[0]["PO"] == pair[1]["PO"] == "AB-12"
        _assert_integrity(result, qb_order)


def test_an_exact_pair_is_accepted_only_when_it_is_unique_one_to_one(qb_mapping, inf_mapping, make_metadata):
    # Two QuickBooks rows carry the identical text "AB-12", so even the exact text is not unique.
    qb_rows = [_qb("AB-12", "", 80.00), _qb("AB-12", "", 80.00)]
    inf_rows = [_inf("AB-12", "", 80.00)]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        assert result.matches == []
        assert set(_ledger(result)["Final Disposition"]) == {DISPOSITION_REVIEW_HOLD}
        _assert_integrity(result, qb_order)


# ---------------------------------------------------------------------------
# 2. Punctuation-insensitive matching is a labelled fallback, and only for a single candidate
# ---------------------------------------------------------------------------

def test_a_single_punctuation_only_candidate_is_accepted_with_the_discrepancy_label(
        qb_mapping, inf_mapping, make_metadata):
    qb_rows = [_qb("AB-12", "", 80.00), _qb("XY-9", "", 15.00)]
    inf_rows = [_inf("AB12", "", 80.00), _inf("XY-9", "", 15.00)]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        assert len(result.matches) == 2
        by_po = {q["PO"]: _ledger(result).loc[f"QB-{n + 1}", "Classification Code"]
                 for n, q in enumerate(qb_order)}
        assert by_po["XY-9"] == CLASS_ACCEPTED_MATCH                              # exact text
        assert by_po["AB-12"] == CLASS_ACCEPTED_MATCH_ID_DISCREPANCY             # fallback, labelled
        fallback = next(g for g in result.matches if g.identifier_discrepancy)
        assert "equal only after ignoring punctuation" in fallback.identifier_discrepancy
        _assert_integrity(result, qb_order)


def test_several_punctuation_candidates_are_held_and_nothing_is_chosen_by_file_order(
        qb_mapping, inf_mapping, make_metadata):
    # "AB12" and "A.B12" both equal "AB-12" only after punctuation is ignored, at the same amount.
    qb_rows = [_qb("AB-12", "", 80.00)]
    inf_rows = [_inf("AB12", "", 80.00), _inf("A.B12", "", 80.00)]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        assert result.matches == []
        row = _ledger(result).loc["QB-1"]
        assert row["Final Disposition"] == DISPOSITION_REVIEW_HOLD
        assert row["In Proposed JE"] == "No"
        assert result.metrics["Proposed JE Amount"] == 0
        _assert_integrity(result, qb_order)


def test_several_quickbooks_rows_competing_for_one_punctuation_candidate_are_all_held(
        qb_mapping, inf_mapping, make_metadata):
    qb_rows = [_qb("AB-12", "", 80.00), _qb("A.B12", "", 80.00)]
    inf_rows = [_inf("AB12", "", 80.00)]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        assert result.matches == []
        assert set(_ledger(result)["Final Disposition"]) == {DISPOSITION_REVIEW_HOLD}
        _assert_integrity(result, qb_order)


def test_a_punctuation_fallback_still_requires_the_exact_signed_cent_amount(qb_mapping, inf_mapping, make_metadata):
    result = _run([_qb("AB-12", "", 80.00)], [_inf("AB12", "", 80.01)], qb_mapping, inf_mapping, make_metadata)
    assert result.matches == []


# ---------------------------------------------------------------------------
# 3. "Specific" is not "unique"
# ---------------------------------------------------------------------------

def test_repeated_no_digit_references_with_the_same_amount_are_held_in_every_file_order(
        qb_mapping, inf_mapping, make_metadata):
    qb_rows = [_qb("LOWES", "", 200.00), _qb("LOWES", "", 200.00)]
    inf_rows = [_inf("LOWES", "", 200.00), _inf("LOWES", "", 200.00)]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        assert result.matches == []
        assert set(_ledger(result)["Final Disposition"]) == {DISPOSITION_REVIEW_HOLD}
        assert (_ledger(result)["In Proposed JE"] == "No").all()
        assert result.metrics["Proposed JE Amount"] == 0
        _assert_integrity(result, qb_order)


def test_the_same_holds_for_a_numeric_reference(qb_mapping, inf_mapping, make_metadata):
    qb_rows = [_qb("5551234", "", 200.00), _qb("5551234", "", 200.00)]
    inf_rows = [_inf("5551234", "", 200.00), _inf("5551234", "", 200.00)]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        assert result.matches == []
        _assert_integrity(result, qb_order)


def test_an_invoice_number_resolves_repeated_references_in_every_file_order(qb_mapping, inf_mapping, make_metadata):
    qb_rows = [_qb("LOWES", "INV-A1", 200.00), _qb("LOWES", "INV-A2", 200.00)]
    inf_rows = [_inf("LOWES", "INV-A1", 200.00), _inf("LOWES", "INV-A2", 200.00)]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        pairs = _pairs(result, qb_order, inf_order)
        assert len(pairs) == 2 and all(q["Invoice"] == i["Invoice"] for q, i in pairs)
        assert all(g.method == "PO + Invoice + Amount" for g in result.matches)
        _assert_integrity(result, qb_order)


def test_dates_do_not_resolve_repeated_references(qb_mapping, inf_mapping, make_metadata):
    # The dates line up one-for-one, which is exactly when a "closest date" guess looks safe. Dates are not a
    # defined matching rule, so the rows are still held.
    qb_rows = [_qb("LOWES", "", 200.00, date="2026-01-05"), _qb("LOWES", "", 200.00, date="2026-01-19")]
    inf_rows = [_inf("LOWES", "", 200.00, date="2026-01-05"), _inf("LOWES", "", 200.00, date="2026-01-19")]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        assert result.matches == []
        assert set(_ledger(result)["Final Disposition"]) == {DISPOSITION_REVIEW_HOLD}
        _assert_integrity(result, qb_order)


def test_a_single_reference_with_a_unique_counterpart_still_matches(qb_mapping, inf_mapping, make_metadata):
    result = _run([_qb("LOWES", "", 200.00)], [_inf("LOWES", "", 200.00)], qb_mapping, inf_mapping, make_metadata)
    assert [g.method for g in result.matches] == ["PO + Amount"]


# ---------------------------------------------------------------------------
# Held items stay out of journal-entry support; every row and dollar is accounted for
# ---------------------------------------------------------------------------

def test_a_mixed_run_accrues_only_the_true_unmatched_row_and_accounts_for_everything(
        qb_mapping, inf_mapping, make_metadata):
    qb_rows = [
        _qb("HP-2231", "", 4860.00), _qb("HP2231", "", 4860.00),        # two exact pairs
        _qb("LOWES", "", 200.00), _qb("LOWES", "", 200.00),            # repeated, unresolved: held
        _qb("AB-12", "", 80.00),                                       # punctuation fallback, one candidate
        _qb("NOSUCH-1", "", 33.00),                                    # genuinely missing from Infinium
    ]
    inf_rows = [
        _inf("HP2231", "", 4860.00), _inf("HP-2231", "", 4860.00),
        _inf("LOWES", "", 200.00), _inf("LOWES", "", 200.00),
        _inf("AB12", "", 80.00),
    ]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        ledger = _ledger(result).copy()
        ledger["PO"] = [row["PO"] for row in qb_order]
        assert (ledger[ledger["PO"] == "LOWES"]["Final Disposition"] == DISPOSITION_REVIEW_HOLD).all()
        assert (ledger[ledger["PO"] == "LOWES"]["In Proposed JE"] == "No").all()
        assert ledger[ledger["PO"] == "NOSUCH-1"]["Final Disposition"].iloc[0] == DISPOSITION_TRUE_UNMATCHED
        paired = ledger[~ledger["PO"].isin(["LOWES", "NOSUCH-1"])]
        assert (paired["Final Disposition"] == DISPOSITION_MATCHED).all() and len(paired) == 3
        assert _cents(result.metrics["Proposed JE Amount"]) == _cents(33.00)    # the held $400.00 is not in the JE
        _assert_integrity(result, qb_order)


@pytest.mark.parametrize("scenario", [
    ([("LOWES", "", 200.0)] * 3, [("LOWES", "", 200.0)] * 2),
    ([("LOWES", "", 200.0)] * 2, [("LOWES", "", 200.0)] * 3),
    ([("AB-12", "", 5.0), ("AB12", "", 5.0), ("A.B12", "", 5.0)], [("AB-12", "", 5.0), ("AB12", "", 5.0)]),
])
def test_no_infinium_row_is_used_twice_however_many_rows_compete(scenario, qb_mapping, inf_mapping, make_metadata):
    qb_rows = [_qb(*fields) for fields in scenario[0]]
    inf_rows = [_inf(*fields) for fields in scenario[1]]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        _assert_integrity(result, qb_order)


# ---------------------------------------------------------------------------
# The documented "PO" prefix normalization (matching-rules.md; clean_po tests in test_matching.py)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("infinium_po", ["PO 2231", "PO-2231", "P.O. 2231", "PO# 2231", "po2231"])
def test_a_leading_po_prefix_is_normalized_away_and_the_match_is_not_labelled_a_discrepancy(
        infinium_po, qb_mapping, inf_mapping, make_metadata):
    result = _run([_qb("2231", "", 90.00)], [_inf(infinium_po, "", 90.00)], qb_mapping, inf_mapping, make_metadata)
    (group,) = result.matches
    assert group.method == "PO + Amount" and not group.identifier_discrepancy
    assert _ledger(result).loc["QB-1", "Classification Code"] == CLASS_ACCEPTED_MATCH


def test_the_prefix_rule_does_not_hide_punctuation_inside_the_identifier(qb_mapping, inf_mapping, make_metadata):
    # "PO-AB-12" normalizes to "AB-12", which still differs from "AB12", so it is a labelled fallback match.
    result = _run([_qb("PO-AB-12", "", 90.00)], [_inf("AB12", "", 90.00)], qb_mapping, inf_mapping, make_metadata)
    (group,) = result.matches
    assert "equal only after ignoring punctuation" in group.identifier_discrepancy
    assert _ledger(result).loc["QB-1", "Classification Code"] == CLASS_ACCEPTED_MATCH_ID_DISCREPANCY


def test_an_invoice_has_no_prefix_rule(qb_mapping, inf_mapping, make_metadata):
    result = _run([_qb("", "INV-77", 90.00)], [_inf("", "77", 90.00)], qb_mapping, inf_mapping, make_metadata)
    assert result.matches == []


# ---------------------------------------------------------------------------
# Evidence priority is PO + invoice, then PO, then invoice; exact-then-fallback happens WITHIN each tier
# ---------------------------------------------------------------------------

def test_a_pair_agreeing_on_po_and_invoice_keeps_the_strongest_method_when_only_the_po_punctuation_differs(
        qb_mapping, inf_mapping, make_metadata):
    # The real-period pattern: invoice identical, PO equal after punctuation (a trailing comma). An exact invoice
    # match must not outrank it and relabel it "Invoice + Amount": PO and invoice both agree.
    qb_rows = [_qb("1162026TX-LOONEY,", "20751", 3670.80)]
    inf_rows = [_inf("PO 1162026TX-LOONEY", "20751", 3670.80)]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        (group,) = result.matches
        assert group.method == "PO + Invoice + Amount"
        assert "equal only after ignoring punctuation" in group.identifier_discrepancy
        assert _ledger(result).loc["QB-1", "Classification Code"] == CLASS_ACCEPTED_MATCH_ID_DISCREPANCY
        _assert_integrity(result, qb_order)


def test_invoices_settle_a_punctuation_collision_in_the_po_in_any_file_order(qb_mapping, inf_mapping, make_metadata):
    qb_rows = [_qb("HP-2231", "I1", 4860.00), _qb("HP2231", "I2", 4860.00)]
    inf_rows = [_inf("HP2231", "I1", 4860.00), _inf("HP-2231", "I2", 4860.00)]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        pairs = _pairs(result, qb_order, inf_order)
        assert len(pairs) == 2 and all(q["Invoice"] == i["Invoice"] for q, i in pairs)
        assert all(g.method == "PO + Invoice + Amount" for g in result.matches)
        _assert_integrity(result, qb_order)


def test_a_po_only_pair_is_still_accepted_when_no_invoice_is_present(qb_mapping, inf_mapping, make_metadata):
    result = _run([_qb("AB-12", "", 80.00)], [_inf("AB-12", "", 80.00)], qb_mapping, inf_mapping, make_metadata)
    assert [g.method for g in result.matches] == ["PO + Amount"]


def test_po_and_invoice_pointing_at_different_records_are_held_not_resolved_by_pass_order(
        qb_mapping, inf_mapping, make_metadata):
    # QuickBooks PO P-1 matches Infinium row A, but its invoice I-1 is on Infinium row B: the identifiers conflict.
    qb_rows = [_qb("P-1", "I-1", 100.00)]
    inf_rows = [_inf("P-1", "I-9", 100.00), _inf("P-9", "I-1", 100.00)]
    for qb_order, inf_order in _both_orders(qb_rows, inf_rows):
        result = _run(qb_order, inf_order, qb_mapping, inf_mapping, make_metadata)
        assert result.matches == []
        assert _ledger(result).loc["QB-1", "Final Disposition"] == DISPOSITION_REVIEW_HOLD
        _assert_integrity(result, qb_order)
