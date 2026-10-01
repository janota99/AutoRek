"""Reason codes and the reference-evidence review holds."""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from ..duplicates import AMOUNT_CENTS, NORM_INV, NORM_PO, SOURCE_POS
from .core import _split_reference_ids, cents_to_float, INF_ID, QB_ID, valid_cents
from .labels import (
    HOLD_AMOUNT_VARIANCE,
    HOLD_CANDIDATE_INVALID_AMOUNT,
    HOLD_EXACT_CANDIDATE_NOT_UNIQUE,
    HOLD_HISTORICAL_CLEARANCE,
    HOLD_INVALID_AMOUNT,
    HOLD_MULTIPLE_CANDIDATES,
    HOLD_PO_ALREADY_REPRESENTED,
    HOLD_PO_REUSE,
    HOLD_TYPO_CANDIDATES,
    MATCH_REF_COLUMN,
    REFERENCE_HOLD_COLUMNS,
    REFERENCED_MATCH_REF_COLUMN,
)
from .exceptions import po_reuse_error_qb_index_map
from .references import describe_match_references


# ---------------------------------------------------------------------------
# Reference-evidence review holds and final QuickBooks dispositions
# ---------------------------------------------------------------------------

# A short, stable code for every Reason Code a workbook displays, plus a
# one-sentence glossary entry -- so a wide sheet can show SHORT_REASON_CODE +
# a concise reason instead of the full audit-grade explanation, with that full
# text one lookup away (see build_reason_code_glossary_sheet) rather than
# widening every row that cites it.

SHORT_REASON_CODES: dict[str, str] = {
    "REVIEW_HOLD_POTENTIAL_DUPLICATE": "POTENTIAL_DUPLICATE",
    "REVIEW_HOLD_ALREADY_REPRESENTED": "POTENTIAL_DUPLICATE",
    "REVIEW_HOLD_PO_ALREADY_REPRESENTED": "PO_ALREADY_REPRESENTED",
    "REVIEW_HOLD_PO_REUSE": "PO_REUSE",
    "REVIEW_HOLD_AMOUNT_VARIANCE": "AMOUNT_VARIANCE",
    "REVIEW_HOLD_EXACT_CANDIDATE_NOT_UNIQUE": "NON_UNIQUE_CANDIDATE",
    "REVIEW_HOLD_MULTIPLE_CANDIDATES": "MULTIPLE_CANDIDATES",
    "REVIEW_HOLD_AMBIGUOUS_CANDIDATES": "MULTIPLE_CANDIDATES",
    "REVIEW_HOLD_TYPO_CANDIDATES": "TYPO_CANDIDATES",
    "REVIEW_HOLD_HISTORICAL_CLEARANCE": "HISTORICAL_CLEARANCE",
    "REVIEW_HOLD_INVALID_AMOUNT": "INVALID_AMOUNT",
    "REVIEW_HOLD_CANDIDATE_INVALID_AMOUNT": "INVALID_AMOUNT",
    "REVIEW_HOLD_FUZZY_MATCH": "FUZZY_CANDIDATE",
    "EXACT_QBO_DUPLICATE_EXCESS_COPY": "DUPLICATE_EXCLUDED",
    "TRUE_UNMATCHED_NO_INFINIUM_CANDIDATE": "NO_INFINIUM_CANDIDATE",
    "TRUE_UNMATCHED_PO_REUSE": "PO_REUSE_UNSUPPORTED",
}


REASON_CODE_GLOSSARY: dict[str, str] = {
    "POTENTIAL_DUPLICATE": (
        "Shares its duplicate key (PO, invoice, and signed amount -- or only a PO or only an "
        "invoice) with another QuickBooks row, but the available evidence does not confirm it is a "
        "copy of the same underlying line. Held, not excluded: it may be a legitimate repeated sale."
    ),
    "PO_ALREADY_REPRESENTED": (
        "Every Infinium record sharing this row's PO or invoice was already consumed by an accepted "
        "match. The transaction may already be represented in the accrual, so it is not double-posted."
    ),
    "PO_REUSE": (
        "This PO is reused across two or more unresolved QuickBooks rows, and their grouped total does "
        "not tie exactly to the grouped Infinium total for that PO -- Infinium has evidence for the PO."
    ),
    "AMOUNT_VARIANCE": (
        "An Infinium record shares this row's PO and/or invoice, but the signed amount differs -- most "
        "likely a data-entry error on one system. Neither amount is posted until the difference is explained."
    ),
    "NON_UNIQUE_CANDIDATE": (
        "An Infinium record agrees on reference and amount, but the pairing is not uniquely one-to-one, "
        "so it is not matched automatically."
    ),
    "MULTIPLE_CANDIDATES": (
        "More than one unresolved Infinium record shares this row's PO and/or invoice. The program will "
        "not guess which one, if any, corresponds to it."
    ),
    "TYPO_CANDIDATES": (
        "More than one Infinium record matches this row's exact amount and differs from its PO only by a "
        "one-character typo, so none is matched automatically."
    ),
    "HISTORICAL_CLEARANCE": (
        "The only apparent Infinium support is a historical record withheld by an unresolved historical "
        "duplicate issue, so it cannot yet be used to clear this row."
    ),
    "INVALID_AMOUNT": "The QuickBooks amount, or the only candidate's Infinium amount, cannot be read as signed cents.",
    "FUZZY_CANDIDATE": (
        "A text-similarity (fuzzy PO) candidate was found with an exact amount match. Always held for "
        "review -- a fuzzy match is a guess, never posted automatically."
    ),
    "DUPLICATE_EXCLUDED": (
        "Confirmed to be a copy of the same underlying transaction as its canonical row -- by a trusted "
        "line-level source ID or a sufficient stable-field fingerprint -- and excluded from the JE."
    ),
    "NO_INFINIUM_CANDIDATE": "No Infinium record shares this row's PO or invoice at all: a genuine missing transaction.",
    "PO_REUSE_UNSUPPORTED": (
        "This PO is reused across unresolved QuickBooks rows and Infinium has no record at all for it -- "
        "a genuine missing transaction, not evidence of an existing match."
    ),
}


def short_reason_code(code: Any) -> str:
    """The compact display code for a Reason Code -- e.g.
    REVIEW_HOLD_EXACT_CANDIDATE_NOT_UNIQUE -> NON_UNIQUE_CANDIDATE -- falling
    back to the code with its REVIEW_HOLD_/TRUE_UNMATCHED_ prefix stripped for
    anything not in the table, so a new code never displays blank."""
    text = str(code or "")
    if text in SHORT_REASON_CODES:
        return SHORT_REASON_CODES[text]
    for prefix in ("REVIEW_HOLD_", "TRUE_UNMATCHED_"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def build_reference_evidence_review_holds(
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    candidates: pd.DataFrame,
    unmatched_qb: list[int],
    register: pd.DataFrame,
    po_reuse_errors: Optional[pd.DataFrame] = None,
    withheld_historical: Optional[pd.DataFrame] = None,
    withheld_group_by_id: Optional[dict[str, str]] = None,
) -> tuple[pd.DataFrame, list[int]]:
    """Hold every still-unmatched QuickBooks row that has evidence of a possible
    Infinium counterpart, instead of accruing it.

    The matching passes decide what CAN be matched; this decides what an
    unmatched row MEANS. A row is a genuine missing transaction (and may feed
    the proposed JE) only when NO Infinium record -- current or withheld
    historical -- supports it. "Unable to establish another match" is never
    "no evidence exists in Infinium". The hold codes are:

      * REVIEW_HOLD_PO_ALREADY_REPRESENTED -- every Infinium record sharing its
        PO/invoice was consumed by an accepted match;
      * REVIEW_HOLD_PO_REUSE -- its PO is reused across several unresolved
        QuickBooks rows and the grouped total does not tie to Infinium;
      * REVIEW_HOLD_AMOUNT_VARIANCE -- one reference candidate remains but its
        signed amount differs;
      * REVIEW_HOLD_EXACT_CANDIDATE_NOT_UNIQUE -- a candidate agrees on amount
        but is not uniquely one-to-one;
      * REVIEW_HOLD_TYPO_CANDIDATES -- more than one candidate satisfies the
        controlled-typo rule, so none is matched automatically;
      * REVIEW_HOLD_HISTORICAL_CLEARANCE -- its only apparent support is a
        historical Infinium record that is withheld by an unresolved
        historical-duplicate issue and so cannot yet be consumed;
      * REVIEW_HOLD_MULTIPLE_CANDIDATES / _CANDIDATE_INVALID_AMOUNT /
        _INVALID_AMOUNT -- correspondence cannot be determined or an amount is
        unreadable.
    """
    empty = pd.DataFrame(columns=REFERENCE_HOLD_COLUMNS)
    if candidates.empty or not unmatched_qb:
        return empty, []
    candidate_by_qb = candidates.set_index("QuickBooks Row ID").to_dict("index")
    inf_amount_by_id = dict(zip(inf[INF_ID], inf[AMOUNT_CENTS]))
    members_by_ref: dict[str, str] = {}
    if register is not None and not register.empty:
        members_by_ref = dict(zip(register[MATCH_REF_COLUMN], register["QuickBooks Row IDs"]))
    po_reuse_id_by_index = po_reuse_error_qb_index_map(po_reuse_errors) if po_reuse_errors is not None else {}
    po_reuse_detail = (
        po_reuse_errors.set_index("PO Reuse ID").to_dict("index")
        if po_reuse_errors is not None and not po_reuse_errors.empty else {}
    )
    withheld_records = (
        withheld_historical[[INF_ID, NORM_PO, NORM_INV, AMOUNT_CENTS]].to_dict("records")
        if withheld_historical is not None and not withheld_historical.empty else []
    )
    withheld_group_by_id = withheld_group_by_id or {}

    def historical_hits(qidx: int) -> list[str]:
        cents, po, invoice = qb.at[qidx, AMOUNT_CENTS], qb.at[qidx, NORM_PO], qb.at[qidx, NORM_INV]
        if not valid_cents(cents):
            return []
        return [
            str(record[INF_ID]) for record in withheld_records
            if valid_cents(record[AMOUNT_CENTS]) and int(record[AMOUNT_CENTS]) == int(cents)
            and ((po and record[NORM_PO] == po) or (invoice and record[NORM_INV] == invoice))
        ]

    records: list[dict[str, Any]] = []
    held: list[int] = []
    for qidx in sorted((int(i) for i in unmatched_qb), key=lambda i: qb.at[i, SOURCE_POS]):
        candidate = candidate_by_qb.get(qb.at[qidx, QB_ID])
        if candidate is None:
            continue
        amount = qb.at[qidx, AMOUNT_CENTS]
        total = int(candidate["Total Candidate Count"])
        available = int(candidate["Available Candidate Count"])
        difference = candidate["Minimum Amount Difference"]
        available_ids = str(candidate["Available Infinium Candidate IDs"] or "")
        used_ids = str(candidate["Already-Matched Candidate IDs"] or "")
        typo_ids = str(candidate.get("Typo Candidate IDs") or "")
        references = _split_reference_ids(candidate.get(REFERENCED_MATCH_REF_COLUMN))
        basis = str(candidate.get("Reference Basis") or "")
        related_inf = available_ids
        inf_amount: Optional[float] = None
        related_qb = ""
        reuse_id = po_reuse_id_by_index.get(qidx)
        hits = historical_hits(qidx)
        if not valid_cents(amount):
            code = HOLD_INVALID_AMOUNT
            classification = "Review Hold — Invalid or Missing QuickBooks Amount"
            explanation = "The QuickBooks amount cannot be read as signed cents, so the row cannot be matched or accrued."
        elif typo_ids:
            code = HOLD_TYPO_CANDIDATES
            related_inf = typo_ids
            classification = "Review Hold — Multiple Controlled-Typo Candidates"
            explanation = (
                "More than one Infinium record matches this row's exact amount and differs from its PO "
                "only by a one-character typo, so none is matched automatically. A reviewer must choose."
            )
        elif reuse_id and total > 0:
            code = HOLD_PO_REUSE
            related_inf = used_ids or available_ids
            related_qb = "; ".join(members_by_ref[r] for r in references if r in members_by_ref)
            classification = "Review Hold — PO Re-use Error (repeated PO; grouped total does not tie to Infinium)"
            explanation = (
                f"{po_reuse_detail.get(reuse_id, {}).get('Explanation', 'This PO is reused across unresolved QuickBooks rows.')} "
                "Because Infinium holds evidence for this PO, the row may already be represented and is "
                "not accrued until a reviewer documents a disposition."
            )
        elif total == 0:
            if not hits:
                continue            # no support anywhere: a genuine unmatched transaction
            code = HOLD_HISTORICAL_CLEARANCE
            related_inf = "; ".join(hits)
            blockers = sorted({withheld_group_by_id.get(h, "") for h in hits} - {""})
            classification = "Review Hold — Historical Clearance Blocked (Withheld Historical Record)"
            explanation = (
                f"The only apparent Infinium support ({related_inf}) is a historical record withheld by an "
                f"unresolved historical duplicate issue{' (' + '; '.join(blockers) + ')' if blockers else ''}, "
                "so it cannot yet be used to clear this row. The row is not accrued until that "
                "relationship is resolved."
            )
        elif available == 0:
            code = HOLD_PO_ALREADY_REPRESENTED
            related_inf = used_ids
            related_qb = "; ".join(members_by_ref[r] for r in references if r in members_by_ref)
            if references:
                label = {"PO": "PO", "Invoice": "Invoice", "Group": "Group Candidate"}.get(basis, "Reference")
                cited = describe_match_references(references, group=basis == "Group")
                classification = f"Review Hold — {label} Already Represented by {cited}"
                explanation = (
                    f"Every Infinium record sharing this row's {label.lower()} was already consumed by "
                    f"{cited}. The transaction may already be represented, so "
                    "it is not accrued until a reviewer decides."
                )
            else:
                classification = "Review Hold — Reference Already Consumed by Another Match Candidate"
                explanation = (
                    "Every Infinium record sharing this row's PO/invoice was consumed by another "
                    "match candidate, so the row is not accrued until a reviewer decides."
                )
        elif available > 1:
            code = HOLD_MULTIPLE_CANDIDATES
            classification = "Review Hold — Multiple Infinium Candidates"
            explanation = (
                f"{available} Infinium records share this row's PO/invoice; the program will not guess "
                "which one, if any, corresponds to it."
            )
        elif difference is None:
            code = HOLD_CANDIDATE_INVALID_AMOUNT
            classification = "Review Hold — Reference Candidate Has an Invalid Amount"
            explanation = "The only Infinium record sharing this row's reference has an unreadable amount."
        elif float(difference) == 0:
            code = HOLD_EXACT_CANDIDATE_NOT_UNIQUE
            classification = "Review Hold — Exact Candidate Not Uniquely Available"
            explanation = (
                "An Infinium record agrees on reference and amount, but the pairing is not uniquely "
                "one-to-one, so it is not matched automatically and the row is not accrued."
            )
        else:
            code = HOLD_AMOUNT_VARIANCE
            classification = "Review Hold — Amount Variance"
            explanation = (
                "An Infinium record shares this row's PO/invoice but its signed amount differs; the "
                "QuickBooks amount is not accrued until the difference is explained."
            )
        if available == 1 and code in (HOLD_AMOUNT_VARIANCE, HOLD_EXACT_CANDIDATE_NOT_UNIQUE, HOLD_PO_REUSE):
            cents = inf_amount_by_id.get(available_ids)
            inf_amount = cents_to_float(cents) if valid_cents(cents) else None
        q_amount = cents_to_float(amount) if valid_cents(amount) else None
        records.append({
            "Hold ID": f"RHOLD-{len(records) + 1:06d}",
            "Reason Code": code,
            "Classification": classification,
            "Confidence": "Review",
            "QuickBooks Row ID": qb.at[qidx, QB_ID],
            "QuickBooks Row Index": qidx,
            "Normalized PO": qb.at[qidx, NORM_PO],
            "Normalized Invoice": qb.at[qidx, NORM_INV],
            "QuickBooks Amount": q_amount,
            "Infinium Amount": inf_amount,
            "Amount Difference": (
                round(q_amount - inf_amount, 2) if q_amount is not None and inf_amount is not None else None
            ),
            "Related Match Ref.": "; ".join(references),
            "Related QuickBooks Row IDs": related_qb,
            "Related Infinium Row IDs": related_inf,
            "Accrual Treatment": "Excluded from automatic JE pending documented disposition",
            "Posting Disposition": "REVIEW REQUIRED - DO NOT POST",
            "Explanation": explanation,
            "Manual Decision": None,
            "Reviewed By": None,
            "Review Timestamp": None,
            "Review Rationale": None,
        })
        held.append(qidx)
    return pd.DataFrame(records, columns=REFERENCE_HOLD_COLUMNS), sorted(held)
