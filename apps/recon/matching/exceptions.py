"""Exception reports built after matching: amount variances, ambiguous duplicate candidates, PO reuse errors."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd

from ..duplicates import AMOUNT_CENTS, SOURCE_POS
from .core import cents_to_float, INF_ID, KEY_INV, KEY_PO, QB_ID, valid_cents
from .engine import _reference_groups


AMOUNT_VARIANCE_COLUMNS = [
    "Variance ID", "Classification", "Confidence", "Reference Evidence",
    "QuickBooks Row ID", "QuickBooks Row Index", "Infinium Row ID",
    "Infinium Row Index", "Normalized PO", "Normalized Invoice",
    "QuickBooks Amount", "Infinium Amount", "Potential Difference",
    "Absolute Difference", "Variance Direction", "Possible Sign Reversal",
    "Mutually Unique Reference", "Accrual Treatment", "Posting Disposition",
    "Explanation", "Manual Decision", "Reviewed By", "Review Timestamp",
    "Review Rationale",
]


def build_reference_amount_variances(
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    unmatched_qb: list[int],
    unmatched_inf: list[int],
) -> tuple[pd.DataFrame, list[int], list[int]]:
    """Identify mutually unique reference matches whose signed amounts differ.

    This is a classification and posting-control pass, never an automatic
    correction. It runs only after primary and historical matching are final.
    A relationship is accepted into the variance-review population only when:

      * both rows remain unresolved;
      * at least one populated normalized PO or invoice agrees exactly;
      * the candidate is unique from QuickBooks to Infinium and Infinium to
        QuickBooks within the remaining populations; and
      * the signed-cent amounts are both valid and differ.

    The full QuickBooks amount and the calculated difference are displayed,
    but neither is automatically posted. Both source rows are moved out of the
    ordinary exception populations and the run remains non-postable until a
    documented disposition is completed and the reconciliation is rerun.
    """
    q_indexes = sorted(set(int(idx) for idx in unmatched_qb))
    i_indexes = sorted(set(int(idx) for idx in unmatched_inf))
    if not q_indexes or not i_indexes:
        return pd.DataFrame(columns=AMOUNT_VARIANCE_COLUMNS), [], []

    # Establish relationships in evidence-strength order. Rows assigned under
    # a stronger tier are removed before the next tier so a reused PO cannot
    # defeat an otherwise unique PO+invoice relationship.
    selected_pairs: list[tuple[int, int, str]] = []
    available_q = set(q_indexes)
    available_i = set(i_indexes)

    def add_unique_tier(fields: tuple[str, ...], tier: str) -> None:
        q_groups = _reference_groups(qb, available_q, fields)
        i_groups = _reference_groups(inf, available_i, fields)
        tier_pairs: list[tuple[int, int, str]] = []
        for key in sorted(set(q_groups).intersection(i_groups)):
            q_rows = q_groups[key]
            i_rows = i_groups[key]
            if len(q_rows) != 1 or len(i_rows) != 1:
                continue
            qidx, iidx = q_rows[0], i_rows[0]
            q_amount = qb.at[qidx, AMOUNT_CENTS]
            i_amount = inf.at[iidx, AMOUNT_CENTS]
            if (
                valid_cents(q_amount)
                and valid_cents(i_amount)
                and int(q_amount) != int(i_amount)
            ):
                tier_pairs.append((qidx, iidx, tier))
        for qidx, iidx, evidence_tier in tier_pairs:
            if qidx in available_q and iidx in available_i:
                selected_pairs.append((qidx, iidx, evidence_tier))
                available_q.remove(qidx)
                available_i.remove(iidx)

    add_unique_tier((KEY_PO, KEY_INV), "PO_AND_INVOICE")
    add_unique_tier((KEY_INV,), "INVOICE_ONLY")
    add_unique_tier((KEY_PO,), "PO_ONLY")

    records: list[dict[str, Any]] = []
    held_q: list[int] = []
    held_i: list[int] = []
    for qidx, iidx, evidence_tier in selected_pairs:
        q_po, i_po = qb.at[qidx, KEY_PO], inf.at[iidx, KEY_PO]
        q_inv, i_inv = qb.at[qidx, KEY_INV], inf.at[iidx, KEY_INV]
        po_agrees = bool(q_po and q_po == i_po)
        invoice_agrees = bool(q_inv and q_inv == i_inv)
        if evidence_tier == "PO_AND_INVOICE":
            classification = "High-likelihood amount variance"
            confidence = "High"
            evidence = "Exact PO and invoice; mutually unique unresolved rows"
        elif evidence_tier == "INVOICE_ONLY":
            classification = "Strong invoice-linked amount variance"
            confidence = "Strong review"
            evidence = "Exact invoice; mutually unique unresolved rows"
        else:
            classification = "PO-linked amount variance"
            confidence = "Moderate review"
            evidence = "Exact PO; mutually unique unresolved rows"
        q_cents = int(qb.at[qidx, AMOUNT_CENTS])
        i_cents = int(inf.at[iidx, AMOUNT_CENTS])
        difference = q_cents - i_cents
        sign_reversal = q_cents == -i_cents and q_cents != 0
        if sign_reversal:
            classification = "Critical possible sign reversal"
            confidence = "High"
        direction = (
            "QuickBooks exceeds Infinium" if difference > 0
            else "Infinium exceeds QuickBooks"
        )
        records.append({
            "Variance ID": f"VAR-{len(records) + 1:06d}",
            "Classification": classification,
            "Confidence": confidence,
            "Reference Evidence": evidence,
            "QuickBooks Row ID": qb.at[qidx, QB_ID],
            "QuickBooks Row Index": qidx,
            "Infinium Row ID": inf.at[iidx, INF_ID],
            "Infinium Row Index": iidx,
            "Normalized PO": q_po if po_agrees else "",
            "Normalized Invoice": q_inv if invoice_agrees else "",
            "QuickBooks Amount": cents_to_float(q_cents),
            "Infinium Amount": cents_to_float(i_cents),
            "Potential Difference": cents_to_float(difference),
            "Absolute Difference": cents_to_float(abs(difference)),
            "Variance Direction": direction,
            "Possible Sign Reversal": sign_reversal,
            "Mutually Unique Reference": True,
            "Accrual Treatment": (
                "Excluded from automatic JE; neither the full amount nor the "
                "difference is posted without documented review"
            ),
            "Posting Disposition": "REVIEW REQUIRED - DO NOT POST",
            "Explanation": (
                "The references indicate a likely common transaction, but signed "
                "amounts differ. The program does not infer which system is correct."
            ),
            "Manual Decision": None,
            "Reviewed By": None,
            "Review Timestamp": None,
            "Review Rationale": None,
        })
        held_q.append(qidx)
        held_i.append(iidx)

    return (
        pd.DataFrame(records, columns=AMOUNT_VARIANCE_COLUMNS),
        sorted(held_q),
        sorted(held_i),
    )


AMBIGUOUS_DUPLICATE_COLUMNS = [
    "Ambiguous ID", "Classification", "Confidence",
    "QuickBooks Row ID", "QuickBooks Row Index", "Normalized PO", "Normalized Invoice",
    "QuickBooks Amount", "Candidate Count", "Candidate Infinium Row IDs",
    "Candidate Infinium Amounts", "Accrual Treatment", "Posting Disposition",
    "Explanation", "Manual Decision", "Reviewed By", "Review Timestamp", "Review Rationale",
]


def build_ambiguous_duplicate_candidates(
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    unmatched_qb: list[int],
    unmatched_inf: list[int],
) -> tuple[pd.DataFrame, list[int]]:
    """Identify unresolved QuickBooks rows whose normalized PO and/or
    invoice is shared by more than one still-unresolved Infinium row.

    This runs after build_reference_amount_variances, against whatever
    remains unresolved on both sides: a row already paired off there (a
    clean, mutually unique 1:1 reference relationship) never reaches here.
    What's left with 2+ textual candidates has no single Infinium row it
    can be tied to at all -- the correct correspondence can't be
    determined from the reference fields alone, so rather than posting the
    QuickBooks amount as an ordinary exception, the row is withheld from
    accrual and itemized here as an ambiguous duplicate pending manual
    research, distinct from both a confirmed (same-side) duplicate and a
    reference-matched amount variance.
    """
    q_indexes = sorted(set(int(idx) for idx in unmatched_qb))
    i_indexes = sorted(set(int(idx) for idx in unmatched_inf))
    if not q_indexes or not i_indexes:
        return pd.DataFrame(columns=AMBIGUOUS_DUPLICATE_COLUMNS), []

    po_groups: dict[str, list[int]] = defaultdict(list)
    inv_groups: dict[str, list[int]] = defaultdict(list)
    for iidx in sorted(i_indexes, key=lambda row: inf.at[row, SOURCE_POS]):
        po = inf.at[iidx, KEY_PO]
        if po:
            po_groups[po].append(iidx)
        invoice = inf.at[iidx, KEY_INV]
        if invoice:
            inv_groups[invoice].append(iidx)

    records: list[dict[str, Any]] = []
    held_q: list[int] = []
    for qidx in q_indexes:
        q_po = qb.at[qidx, KEY_PO]
        q_inv = qb.at[qidx, KEY_INV]
        candidate_indexes: set[int] = set(po_groups.get(q_po, [])) if q_po else set()
        if q_inv:
            candidate_indexes.update(inv_groups.get(q_inv, []))
        if len(candidate_indexes) < 2:
            continue
        candidate_list = sorted(candidate_indexes, key=lambda row: inf.at[row, SOURCE_POS])
        q_amount = qb.at[qidx, AMOUNT_CENTS]
        records.append({
            "Ambiguous ID": f"AMBIG-{len(records) + 1:06d}",
            "Classification": "Ambiguous Duplicate - Multiple Candidates",
            "Confidence": "Review",
            "QuickBooks Row ID": qb.at[qidx, QB_ID],
            "QuickBooks Row Index": qidx,
            "Normalized PO": q_po,
            "Normalized Invoice": q_inv,
            "QuickBooks Amount": cents_to_float(q_amount) if valid_cents(q_amount) else None,
            "Candidate Count": len(candidate_list),
            "Candidate Infinium Row IDs": "; ".join(str(inf.at[iidx, INF_ID]) for iidx in candidate_list),
            "Candidate Infinium Amounts": "; ".join(
                f"{cents_to_float(inf.at[iidx, AMOUNT_CENTS]):,.2f}"
                if valid_cents(inf.at[iidx, AMOUNT_CENTS]) else "invalid/missing"
                for iidx in candidate_list
            ),
            "Accrual Treatment": (
                "Excluded from automatic JE; the correct Infinium correspondence cannot "
                "be determined from the reference fields alone"
            ),
            "Posting Disposition": "REVIEW REQUIRED - DO NOT POST",
            "Explanation": (
                f"{len(candidate_list)} unresolved Infinium rows share this row's normalized PO "
                "and/or invoice. The program will not guess which one, if any, corresponds to "
                "this QuickBooks row."
            ),
            "Manual Decision": None,
            "Reviewed By": None,
            "Review Timestamp": None,
            "Review Rationale": None,
        })
        held_q.append(qidx)

    return pd.DataFrame(records, columns=AMBIGUOUS_DUPLICATE_COLUMNS), sorted(held_q)


PO_REUSE_ERROR_COLUMNS = [
    "PO Reuse ID", "Normalized PO", "QuickBooks Total", "Infinium Total",
    "Difference", "QuickBooks Row Count", "Infinium Row Count",
    "QuickBooks Row IDs", "QuickBooks Row Indexes",
    "Infinium Row IDs", "Infinium Row Indexes",
    "Explanation",
]


def build_po_reuse_errors(
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    unmatched_qb: list[int],
    unmatched_inf: list[int],
) -> pd.DataFrame:
    """Identify a normalized PO reused across 2+ rows in the remaining
    unresolved QuickBooks pool whose grouped total does not tie exactly to
    the grouped Infinium total for the same PO among the rows still
    unresolved there.

    This is a classification and reporting pass, not a matching pass: a PO
    whose grouped totals DO tie exactly was already accepted as a real
    match by the grouped-aggregate pass in perform_matching (see
    _group_candidates) and never reaches here unresolved. Rows flagged
    here are not removed from unmatched_qb by this function; it gives them a
    traceable classification and grouped detail. build_reference_evidence_
    review_holds then decides each row's fate: a row with Infinium evidence
    for the PO is held (REVIEW_HOLD_PO_REUSE) and kept out of the proposed
    JE; a row with none stays a true unmatched transaction.

    Zero tolerance is applied (a difference of even one cent is flagged),
    consistent with this application's established "no tolerance" amount
    policy.
    """
    q_indexes = sorted(set(int(idx) for idx in unmatched_qb))
    i_indexes = sorted(set(int(idx) for idx in unmatched_inf))
    if not q_indexes:
        return pd.DataFrame(columns=PO_REUSE_ERROR_COLUMNS)

    q_po_groups: dict[str, list[int]] = defaultdict(list)
    for idx in sorted(q_indexes, key=lambda row: qb.at[row, SOURCE_POS]):
        po = qb.at[idx, KEY_PO]
        if po:
            q_po_groups[po].append(idx)

    i_po_groups: dict[str, list[int]] = defaultdict(list)
    for idx in sorted(i_indexes, key=lambda row: inf.at[row, SOURCE_POS]):
        po = inf.at[idx, KEY_PO]
        if po:
            i_po_groups[po].append(idx)

    records: list[dict[str, Any]] = []
    for po, q_rows in q_po_groups.items():
        if len(q_rows) < 2:
            continue
        if any(not valid_cents(qb.at[idx, AMOUNT_CENTS]) for idx in q_rows):
            # An invalid/missing amount already has its own dedicated
            # classification; a grouped total built partly from a missing
            # value would be misleading, not a real re-use error.
            continue
        q_total = sum(int(qb.at[idx, AMOUNT_CENTS]) for idx in q_rows)
        i_rows = [idx for idx in i_po_groups.get(po, []) if valid_cents(inf.at[idx, AMOUNT_CENTS])]
        i_total = sum(int(inf.at[idx, AMOUNT_CENTS]) for idx in i_rows)
        difference = q_total - i_total
        if difference == 0:
            # Grouped totals agree exactly -- preserve whatever resolution
            # or review treatment already applies; this is not an error.
            continue
        records.append({
            "PO Reuse ID": f"POREUSE-{len(records) + 1:06d}",
            "Normalized PO": po,
            "QuickBooks Total": cents_to_float(q_total),
            "Infinium Total": cents_to_float(i_total),
            "Difference": cents_to_float(difference),
            "QuickBooks Row Count": len(q_rows),
            "Infinium Row Count": len(i_rows),
            "QuickBooks Row IDs": "; ".join(qb.at[idx, QB_ID] for idx in q_rows),
            "QuickBooks Row Indexes": "; ".join(str(idx) for idx in q_rows),
            "Infinium Row IDs": "; ".join(inf.at[idx, INF_ID] for idx in i_rows) if i_rows else "(none)",
            "Infinium Row Indexes": "; ".join(str(idx) for idx in i_rows),
            "Explanation": (
                f"PO {po} appears {len(q_rows)} times in the unresolved QuickBooks pool with a "
                f"grouped total of {cents_to_float(q_total)}. The corresponding Infinium total for "
                f"this PO among unresolved Infinium rows is {cents_to_float(i_total)} -- a difference "
                f"of {cents_to_float(difference)}. No tolerance is applied. Rows with Infinium evidence for "
                "this PO are held for review (REVIEW_HOLD_PO_REUSE) and not accrued; a row with none "
                "remains a true unmatched transaction."
            ),
        })
    return pd.DataFrame(records, columns=PO_REUSE_ERROR_COLUMNS)


def po_reuse_error_qb_index_map(po_reuse_errors: pd.DataFrame) -> dict[int, str]:
    """Explode the grouped PO Re-use Error report into a per-QuickBooks-row
    lookup (row index -> PO Reuse ID), for row-level Exception Cause/Status
    labeling in build_paired_rows and the Unresolved Exceptions worksheet."""
    mapping: dict[int, str] = {}
    if po_reuse_errors.empty:
        return mapping
    for record in po_reuse_errors.to_dict("records"):
        for token in str(record["QuickBooks Row Indexes"]).split(";"):
            token = token.strip()
            if token:
                mapping[int(token)] = str(record["PO Reuse ID"])
    return mapping
