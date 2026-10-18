"""Pinned facts behind known issues 3 and 4 (identifier normalization and name-only references).

What these tests lock in is what is TRUE regardless of the matching policy: punctuation collapses to one comparison key,
a punctuation difference is always recorded on the match, amounts must agree to the cent, and a reference without
digits is still "specific". How such rows are PAIRED (exact text first, punctuation-insensitive only as a fallback,
never by file order) is tested in test_exact_first_matching.py. Synthetic data only.
"""

from __future__ import annotations

import pandas as pd
import pytest

from apps.recon.matching import build_reconciliation, clean_po, identifier_flags, reference_strength
from apps.recon.matching.labels import (
    CLASS_ACCEPTED_MATCH,
    CLASS_ACCEPTED_MATCH_ID_DISCREPANCY,
    DISPOSITION_MATCHED,
)


def _qb(po, invoice, amount, customer="Acme", date="2026-01-05"):
    return {"PO": po, "Invoice": invoice, "Amount": amount, "Qty": 1, "Period": "1",
            "Customer": customer, "Date": date, "Memo": ""}


def _inf(po, invoice, amount, customer="Acme", date="2026-01-05", period="1"):
    return {"PO": po, "Invoice": invoice, "Amount": amount, "Period": period,
            "Customer": customer, "Date": date}


def _run(qb_rows, inf_rows, qb_mapping, inf_mapping, make_metadata):
    return build_reconciliation(
        pd.DataFrame(qb_rows), pd.DataFrame(inf_rows), qb_mapping, inf_mapping, make_metadata(), 2026,
    )


def _ledger(result):
    return result.qb_dispositions.set_index("QBO Row ID")


# ---------------------------------------------------------------------------
# Known issue 4: punctuation is collapsed in the comparison key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("left,right", [("AB-12", "AB12"), ("1-23", "123"), ("PO# 9-9", "99"), ("A.B/C", "ABC")])
def test_identifiers_that_differ_only_by_punctuation_share_one_comparison_key(left, right):
    assert clean_po(left) == clean_po(right)
    assert "PUNCTUATION_IGNORED" in identifier_flags(left) or left == right


def test_a_punctuation_only_difference_is_accepted_but_labelled_so_a_reviewer_can_see_it(
        qb_mapping, inf_mapping, make_metadata):
    result = _run([_qb("1-23", "I1", 50.0)], [_inf("123", "I1", 50.0)], qb_mapping, inf_mapping, make_metadata)
    (group,) = result.matches
    assert "equal only after ignoring punctuation" in group.identifier_discrepancy
    row = _ledger(result).loc["QB-1"]
    assert row["Final Disposition"] == DISPOSITION_MATCHED
    assert row["Classification Code"] == CLASS_ACCEPTED_MATCH_ID_DISCREPANCY   # not the clean class


def test_a_collision_never_lets_one_infinium_row_be_used_by_two_quickbooks_rows(
        qb_mapping, inf_mapping, make_metadata):
    result = _run(
        [_qb("AB-12", "", 80.0), _qb("AB12", "", 80.0)],
        [_inf("AB12", "", 80.0)],
        qb_mapping, inf_mapping, make_metadata,
    )
    used = [index for group in result.matches for index in group.inf_rows]
    assert len(used) == len(set(used)), "an Infinium row was consumed twice"


def test_different_amounts_stop_a_punctuation_collision_from_matching(qb_mapping, inf_mapping, make_metadata):
    result = _run([_qb("AB-12", "I1", 80.0)], [_inf("AB12", "I1", 80.01)], qb_mapping, inf_mapping, make_metadata)
    assert result.matches == []                                  # amounts must agree to the cent


# ---------------------------------------------------------------------------
# Known issue 3: references without digits are not "weak" today
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["HOPPER", "LOWES", "ACME", "SMITHELECTRIC"])
def test_a_reference_with_no_digits_counts_as_specific_today(value):
    assert reference_strength(clean_po(value)) == "SPECIFIC"


@pytest.mark.parametrize("value", ["NA", "TBD", "MISC", "PENDING"])
def test_the_placeholder_list_is_the_only_word_list_that_makes_a_reference_weak(value):
    assert reference_strength(clean_po(value)) == "WEAK"


def test_a_name_only_po_with_an_exact_amount_is_accepted_like_any_other_po(qb_mapping, inf_mapping, make_metadata):
    result = _run([_qb("HOPPER", "", 120.0)], [_inf("HOPPER", "", 120.0)], qb_mapping, inf_mapping, make_metadata)
    (group,) = result.matches
    assert group.method == "PO + Amount"
    assert _ledger(result).loc["QB-1", "Classification Code"] == CLASS_ACCEPTED_MATCH


def test_a_name_only_po_never_matches_on_a_different_amount(qb_mapping, inf_mapping, make_metadata):
    result = _run([_qb("HOPPER", "", 120.0)], [_inf("HOPPER", "", 119.99)], qb_mapping, inf_mapping, make_metadata)
    assert result.matches == []
