"""Working-frame preparation and ``perform_matching``: every exact, grouped, and fuzzy matching pass."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import pandas as pd

from ..duplicates import AMOUNT_CENTS, LINE_ID, NORM_INV, NORM_PO, SOURCE_POS, TXN_ID
from ..fuzzy_po_matching import (
    find_controlled_typo_matches,
    find_fuzzy_po_matches,
    FUZZY_PO_CONFIDENCE,
    FUZZY_PO_EXPLANATION,
    FUZZY_PO_METHOD,
    PO_TOKENS,
    significant_po_tokens,
    TYPO_PO_CONFIDENCE,
    TYPO_PO_METHOD,
)
from ..vendor_aliases import (
    ALIAS_CONFIDENCE,
    alias_explanation,
    ALIAS_METHOD,
    build_alias_token_map,
    find_alias_po_matches,
    VendorAlias,
)
from .core import (
    cents_to_float,
    clean_alphanumeric,
    clean_po,
    FISCAL_LABEL,
    fiscal_label,
    INF_ID,
    MatchGroup,
    MAX_GROUP_POOL_ROWS,
    MAX_GROUP_SIZE,
    parse_amount_cents,
    product_match,
    PRODUCT_STANDARD,
    QB_ID,
    valid_cents,
)
from .labels import _ALREADY_MATCHED_DISPOSITION


def prepare_working_frame(
    raw: pd.DataFrame,
    mapping: dict[str, Optional[str]],
    source: str,
    fiscal_year: int,
    id_prefix: Optional[str] = None,
) -> pd.DataFrame:
    frame = raw.copy().reset_index(drop=True)
    frame[SOURCE_POS] = range(len(frame))
    id_column = QB_ID if source == "QB" else INF_ID
    prefix = id_prefix or source
    frame[id_column] = [f"{prefix}-{row + 1}" for row in range(len(frame))]
    frame[NORM_PO] = frame[mapping["po"]].map(clean_po)
    frame[NORM_INV] = frame[mapping["invoice"]].map(clean_alphanumeric)
    frame[AMOUNT_CENTS] = frame[mapping["amount"]].map(parse_amount_cents)
    frame[PO_TOKENS] = frame[mapping["po"]].map(significant_po_tokens)
    # Optional source IDs (see duplicates._decisions): a LINE-level ID (one per
    # source row) can independently confirm a copied row -- if the data does not
    # contradict that it is line-level; a transaction/invoice-level ID only
    # supports a fingerprint match.
    for role, target in (("line_id", LINE_ID), ("transaction_id", TXN_ID)):
        id_column = mapping.get(role)
        if id_column and id_column in frame.columns:
            frame[target] = frame[id_column].map(lambda value: "" if pd.isna(value) else str(value).strip())
    if source == "QB":
        product_col = mapping.get("product")
        period_col = mapping.get("period")
        frame[PRODUCT_STANDARD] = frame[product_col].map(product_match) if product_col else None
        if period_col:
            frame[FISCAL_LABEL] = frame[period_col].map(lambda value: fiscal_label(value, fiscal_year))
        else:
            frame[FISCAL_LABEL] = "Unspecified"
    return frame


def _subset_solutions_for_target(
    frame: pd.DataFrame,
    indexes: list[int],
    target_cents: int,
    solution_limit: int = 2,
) -> list[tuple[int, ...]]:
    """Return up to ``solution_limit`` exact multi-row subsets.

    Retaining at most two paths per subtotal is sufficient because grouped
    matching only needs to distinguish a unique solution from an ambiguous
    one. Pools outside the documented bounds are left for manual review.
    """
    usable = [
        int(idx) for idx in indexes
        if valid_cents(frame.at[idx, AMOUNT_CENTS])
    ]
    usable.sort(key=lambda idx: frame.at[idx, SOURCE_POS])
    if len(usable) < 2 or len(usable) > MAX_GROUP_POOL_ROWS:
        return []

    states: dict[tuple[int, int], list[tuple[int, ...]]] = {(0, 0): [()]}
    for idx in usable:
        amount = int(frame.at[idx, AMOUNT_CENTS])
        additions: dict[tuple[int, int], list[tuple[int, ...]]] = defaultdict(list)
        for (subtotal, size), paths in list(states.items()):
            if size >= MAX_GROUP_SIZE:
                continue
            key = (subtotal + amount, size + 1)
            for path in paths:
                candidate = (*path, idx)
                if candidate not in additions[key]:
                    additions[key].append(candidate)
                if len(additions[key]) >= solution_limit:
                    break
        for key, paths in additions.items():
            existing = states.setdefault(key, [])
            for path in paths:
                if path not in existing:
                    existing.append(path)
                if len(existing) >= solution_limit:
                    break

    solutions: list[tuple[int, ...]] = []
    for size in range(2, MAX_GROUP_SIZE + 1):
        for path in states.get((int(target_cents), size), []):
            if path not in solutions:
                solutions.append(path)
            if len(solutions) >= solution_limit:
                return solutions
    return solutions


def _reference_groups(
    frame: pd.DataFrame,
    remaining: set[int],
    fields: tuple[str, ...],
) -> dict[tuple[str, ...], list[int]]:
    groups: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for idx in sorted(remaining, key=lambda row: frame.at[row, SOURCE_POS]):
        if not valid_cents(frame.at[idx, AMOUNT_CENTS]):
            continue
        key = tuple(str(frame.at[idx, field]) for field in fields)
        if any(not value for value in key):
            continue
        groups[key].append(int(idx))
    return groups


def _group_candidates(
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    remaining_q: set[int],
    remaining_i: set[int],
    fields: tuple[str, ...],
) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    """Build exact one-to-many and many-to-one candidates for one rule pass."""
    q_groups = _reference_groups(qb, remaining_q, fields)
    i_groups = _reference_groups(inf, remaining_i, fields)
    candidates: set[tuple[tuple[int, ...], tuple[int, ...]]] = set()

    for key in sorted(set(q_groups).intersection(i_groups)):
        q_indexes = q_groups[key]
        i_indexes = i_groups[key]
        q_solutions_by_target: dict[int, list[tuple[int, ...]]] = {}
        i_solutions_by_target: dict[int, list[tuple[int, ...]]] = {}
        for iidx in i_indexes:
            target = int(inf.at[iidx, AMOUNT_CENTS])
            if target not in q_solutions_by_target:
                q_solutions_by_target[target] = _subset_solutions_for_target(
                    qb, q_indexes, target
                )
            for q_subset in q_solutions_by_target[target]:
                candidates.add((tuple(q_subset), (int(iidx),)))
        for qidx in q_indexes:
            target = int(qb.at[qidx, AMOUNT_CENTS])
            if target not in i_solutions_by_target:
                i_solutions_by_target[target] = _subset_solutions_for_target(
                    inf, i_indexes, target
                )
            for i_subset in i_solutions_by_target[target]:
                candidates.add(((int(qidx),), tuple(i_subset)))

    return sorted(
        candidates,
        key=lambda pair: (
            min(qb.at[idx, SOURCE_POS] for idx in pair[0]),
            min(inf.at[idx, SOURCE_POS] for idx in pair[1]),
            pair,
        ),
    )


def _duplicate_cluster_key(frame: pd.DataFrame, idx: int) -> Optional[tuple[str, str, str, int]]:
    """The identity a row shares with every other row it is genuinely
    indistinguishable from: full PO+Invoice+Amount agreement, or PO+Amount
    agreement with invoice blank on the row (nothing is being ignored --
    there simply is no invoice), or the symmetric Invoice+Amount case."""
    amount = frame.at[idx, AMOUNT_CENTS]
    if pd.isna(amount):
        return None
    po = frame.at[idx, NORM_PO]
    invoice = frame.at[idx, NORM_INV]
    has_po = bool(po)
    has_invoice = bool(invoice)
    if has_po and has_invoice:
        return ("STRICT", str(po), str(invoice), int(amount))
    if has_po:
        return ("PO_ONLY", str(po), "", int(amount))
    if has_invoice:
        return ("INVOICE_ONLY", "", str(invoice), int(amount))
    return None


_CLUSTER_KEY_METHOD = {
    "STRICT": ("PO + Invoice + Amount", "Strong"),
    "PO_ONLY": ("PO + Amount", "Moderate"),
    "INVOICE_ONLY": ("Invoice + Amount", "Strong"),
}


def _duplicate_cluster_pairs(
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    remaining_q: set[int],
    remaining_i: set[int],
) -> list[MatchGroup]:
    """Pair up to min(count) rows within a same-key cluster of rows that
    are genuinely indistinguishable from each other -- e.g. two QuickBooks
    rows sharing the exact same PO, invoice, and amount. The ordinary
    one-to-one passes above intentionally refuse to guess among candidates
    that AREN'T uniquely identifiable (there could be a real difference
    between them the pass just can't see), and that stays exactly as
    conservative as before. This is a different situation: cluster members
    carry no distinguishing information at all, so pairing them by stable
    source-file order is exactly as correct as any other pairing -- there
    is nothing to get wrong. Only fires for a key with 2+ rows on at least
    one side; a key that is already unique on both sides was already
    matched (or not) by the ordinary passes above.
    """
    q_groups: dict[tuple, list[int]] = defaultdict(list)
    for idx in sorted(remaining_q, key=lambda i: qb.at[i, SOURCE_POS]):
        key = _duplicate_cluster_key(qb, idx)
        if key is not None:
            q_groups[key].append(idx)
    i_groups: dict[tuple, list[int]] = defaultdict(list)
    for idx in sorted(remaining_i, key=lambda i: inf.at[i, SOURCE_POS]):
        key = _duplicate_cluster_key(inf, idx)
        if key is not None:
            i_groups[key].append(idx)

    groups: list[MatchGroup] = []
    for key, q_list in q_groups.items():
        i_list = i_groups.get(key, [])
        if len(q_list) < 2 and len(i_list) < 2:
            continue
        method, confidence = _CLUSTER_KEY_METHOD[key[0]]
        explanation = (
            f"Unique {method.lower()} agreement is not possible here because more than one row "
            "shares the exact same key -- but every row sharing it is financially identical, so "
            "pairing them in stable source order is exactly as correct as any other pairing. "
            "Any group member left over is accounted for separately rather than assumed away."
        )
        for qidx, iidx in zip(q_list, i_list):
            groups.append(MatchGroup([qidx], [iidx], method, confidence, explanation))
    return groups


def perform_matching(
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    *,
    enable_fuzzy: bool = True,
    vendor_aliases: Optional[list[VendorAlias]] = None,
) -> tuple[list[MatchGroup], list[int], list[int], pd.DataFrame]:
    """Match one-to-one first, then unique exact aggregate relationships."""
    remaining_q = set(qb.index)
    remaining_i = set(inf.index)
    matches: list[MatchGroup] = []

    passes = [
        ((NORM_PO, NORM_INV, AMOUNT_CENTS), "PO + Invoice + Amount", "Strong",
         "Unique normalized PO, invoice, and signed amount agree."),
        ((NORM_PO, AMOUNT_CENTS), "PO + Amount", "Moderate",
         "Unique normalized PO and signed amount agree."),
        ((NORM_INV, AMOUNT_CENTS), "Invoice + Amount", "Strong",
         "Unique normalized invoice and signed amount agree -- invoice number is the "
         "designated secondary reconciliation check, so a unique match on it carries the "
         "same confidence as a PO match even when the PO itself doesn't agree."),
    ]

    def unique_rows(
        frame: pd.DataFrame,
        remaining: set[int],
        fields: tuple[str, ...],
        index_column: str,
    ) -> pd.DataFrame:
        if not remaining:
            return pd.DataFrame(columns=[*fields, index_column])
        subset = frame.loc[sorted(remaining), list(fields)].copy()
        valid = subset.notna().all(axis=1) & subset.ne("").all(axis=1)
        subset = subset.loc[valid]
        subset[index_column] = subset.index
        return subset.drop_duplicates(subset=list(fields), keep=False)

    for fields, method, confidence, explanation in passes:
        q_unique = unique_rows(qb, remaining_q, fields, "__QB_INDEX")
        i_unique = unique_rows(inf, remaining_i, fields, "__INF_INDEX")
        if q_unique.empty or i_unique.empty:
            continue
        pairs = q_unique.merge(
            i_unique,
            on=list(fields),
            how="inner",
            sort=False,
            validate="one_to_one",
        ).sort_values("__QB_INDEX")
        pair_indexes = list(
            pairs[["__QB_INDEX", "__INF_INDEX"]].itertuples(index=False, name=None)
        )
        matches.extend(
            MatchGroup([int(qidx)], [int(iidx)], method, confidence, explanation)
            for qidx, iidx in pair_indexes
        )
        remaining_q.difference_update(int(qidx) for qidx, _ in pair_indexes)
        remaining_i.difference_update(int(iidx) for _, iidx in pair_indexes)

    # Same-key duplicate clusters (see _duplicate_cluster_pairs): rows that
    # couldn't match above purely because they weren't globally unique, even
    # though they carry no distinguishing information from their siblings.
    for group in _duplicate_cluster_pairs(qb, inf, remaining_q, remaining_i):
        matches.append(group)
        remaining_q.difference_update(group.qb_rows)
        remaining_i.difference_update(group.inf_rows)

    grouped_passes = [
        (
            (NORM_PO, NORM_INV),
            "PO + Invoice + Aggregate Amount (Grouped)",
            "Moderate",
            "After all one-to-one passes, one unique group shares the normalized "
            "PO and invoice and agrees to the opposing row's exact signed-cent total.",
        ),
        (
            (NORM_PO,),
            "PO + Aggregate Amount (Grouped)",
            "Moderate",
            "After all one-to-one passes, one unique group shares the normalized PO "
            "and agrees to the opposing row's exact signed-cent total.",
        ),
        (
            (NORM_INV,),
            "Invoice + Aggregate Amount (Grouped)",
            "Moderate",
            "After all one-to-one passes, one unique group shares the normalized "
            "invoice and agrees to the opposing row's exact signed-cent total.",
        ),
    ]

    for fields, method, confidence, explanation in grouped_passes:
        while remaining_q and remaining_i:
            possible = _group_candidates(
                qb, inf, remaining_q, remaining_i, fields
            )
            if not possible:
                break
            q_occurrences = Counter(idx for q_rows, _ in possible for idx in q_rows)
            i_occurrences = Counter(idx for _, i_rows in possible for idx in i_rows)
            accepted = [
                (q_rows, i_rows)
                for q_rows, i_rows in possible
                if all(q_occurrences[idx] == 1 for idx in q_rows)
                and all(i_occurrences[idx] == 1 for idx in i_rows)
            ]
            if not accepted:
                break
            for q_rows, i_rows in accepted:
                matches.append(
                    MatchGroup(
                        list(q_rows),
                        list(i_rows),
                        method,
                        confidence,
                        explanation,
                        group_level=True,
                    )
                )
                remaining_q.difference_update(q_rows)
                remaining_i.difference_update(i_rows)

    # A confirmed vendor alias (e.g. QuickBooks "Hopper" and Infinium
    # "David" -- the same dock-sale customer referenced by surname on one
    # system and first name on the other) is a decided fact, not a guess,
    # so it is checked before the fuzzy pass and posted like any other
    # exact match rather than held for review -- see vendor_aliases.py.
    # Runs whether or not fuzzy matching is enabled for this call: an
    # alias carries none of the text-similarity risk that justifies
    # skipping fuzzy matching for a historical/secondary population.
    if vendor_aliases:
        alias_token_map = build_alias_token_map(vendor_aliases)
        alias_groups = find_alias_po_matches(qb, inf, remaining_q, remaining_i, alias_token_map)
        for q_group, i_group in alias_groups:
            matches.append(
                MatchGroup(
                    list(q_group), list(i_group), ALIAS_METHOD, ALIAS_CONFIDENCE,
                    alias_explanation(qb, inf, q_group, i_group, vendor_aliases, alias_token_map),
                    group_level=len(q_group) > 1 or len(i_group) > 1,
                )
            )
            remaining_q.difference_update(q_group)
            remaining_i.difference_update(i_group)

    # After every exact one-to-one and grouped-aggregate pass, a small,
    # tightly-bounded fuzzy PO pass covers rows whose PO field is a buyer
    # name or ad hoc note rather than a clean PO number (e.g. QuickBooks
    # "Hopper" vs Infinium "DAVID HOPPER 2.2"). The signed amount must
    # still agree exactly, and only unique whole-word-containment matches
    # are accepted -- see fuzzy_po_matching.py for the full rule. Disabled
    # when matching historical/secondary data against the primary population
    # -- a text-similarity guess is a much bigger risk against a population
    # that isn't the accrual source of truth, so it's simply not attempted
    # there rather than held for review.
    if enable_fuzzy:
        fuzzy_groups = find_fuzzy_po_matches(qb, inf, remaining_q, remaining_i)
        for q_group, i_group in fuzzy_groups:
            matches.append(
                MatchGroup(
                    list(q_group), list(i_group), FUZZY_PO_METHOD, FUZZY_PO_CONFIDENCE,
                    FUZZY_PO_EXPLANATION, group_level=len(q_group) > 1 or len(i_group) > 1,
                )
            )
            remaining_q.difference_update(q_group)
            remaining_i.difference_update(i_group)

    # Controlled PO typo (see fuzzy_po_matching.controlled_typo_pair): a separate,
    # explicitly labeled rule for a one-character misspelling inside an otherwise
    # identical reference, e.g. ELIOT / ELLIOT. Exact signed amount, only within
    # what is still unmatched, exactly one qualifying candidate on each side. A row
    # with more than one candidate is NOT matched; it is recorded for review.
    typo_review: dict[int, list[int]] = {}
    if enable_fuzzy:
        typo_pairs, typo_review = find_controlled_typo_matches(qb, inf, remaining_q, remaining_i)
        for qidx, iidx, (word_q, word_i) in typo_pairs:
            matches.append(
                MatchGroup(
                    [qidx], [iidx], TYPO_PO_METHOD, TYPO_PO_CONFIDENCE,
                    f"Controlled typo: the signed amount agrees exactly, each row is the only "
                    f"qualifying candidate for the other, and the PO references match word for word "
                    f"except '{word_q}' (QuickBooks) vs '{word_i}' (Infinium) -- a one-character "
                    "difference. Accepted under an explicitly labeled rule; this is not an exact match.",
                )
            )
            remaining_q.discard(qidx)
            remaining_i.discard(iidx)
        typo_review = {q: cands for q, cands in typo_review.items() if q in remaining_q}

    matches.sort(
        key=lambda group: min(
            qb.at[idx, SOURCE_POS] for idx in group.qb_rows
        ) if group.qb_rows else 10**12
    )
    for number, group in enumerate(matches, 1):
        group.match_id = f"M-{number:06d}"

    candidate_columns = [
        "QuickBooks Row ID", "Exception Cause", "Cause Confidence",
        "Disposition", "Total Candidate Count",
        "Available Candidate Count", "Available Infinium Candidate IDs",
        "Already-Matched Candidate IDs", "Minimum Amount Difference", "Typo Candidate IDs",
    ]
    q_rows = sorted(remaining_q)
    if not q_rows:
        candidates = pd.DataFrame(columns=candidate_columns)
        return matches, q_rows, sorted(remaining_i), candidates

    inf_reference = inf[[INF_ID, NORM_PO, NORM_INV, AMOUNT_CENTS]].copy()
    inf_reference["__IIDX"] = inf_reference.index

    def reference_groups(field: str) -> dict[str, list[int]]:
        populated = inf_reference.loc[inf_reference[field].ne("")]
        if populated.empty:
            return {}
        return populated.groupby(field, sort=False)["__IIDX"].agg(list).to_dict()

    po_groups = reference_groups(NORM_PO)
    invoice_groups = reference_groups(NORM_INV)

    def format_candidate_ids(indexes: list[int]) -> str:
        display_limit = 25
        displayed = [str(inf.at[idx, INF_ID]) for idx in indexes[:display_limit]]
        if len(indexes) > display_limit:
            displayed.append(f"... ({len(indexes) - display_limit} more)")
        return "; ".join(displayed)

    candidate_records: list[dict[str, Any]] = []
    for qidx in q_rows:
        candidate_indexes = set(po_groups.get(qb.at[qidx, NORM_PO], [])) if qb.at[qidx, NORM_PO] else set()
        if qb.at[qidx, NORM_INV]:
            candidate_indexes.update(invoice_groups.get(qb.at[qidx, NORM_INV], []))
        available = sorted(candidate_indexes.intersection(remaining_i))
        used = sorted(candidate_indexes.difference(remaining_i))
        qamount = qb.at[qidx, AMOUNT_CENTS]
        differences = [
            abs(int(qamount) - int(inf.at[iidx, AMOUNT_CENTS]))
            for iidx in available
            if valid_cents(qamount) and valid_cents(inf.at[iidx, AMOUNT_CENTS])
        ]
        minimum_difference = min(differences) if differences else None
        typo_candidates = typo_review.get(qidx, [])
        if not valid_cents(qamount):
            reason = "Invalid or missing QuickBooks amount"
            exception_cause, cause_confidence = "Invalid QuickBooks amount", "High"
        elif typo_candidates:
            reason = "Review: more than one controlled-typo candidate"
            exception_cause, cause_confidence = "Potential Match - Multiple controlled-typo candidates", "Review"
        elif len(available) > 1:
            reason = "Duplicate or ambiguous Infinium values - multiple candidates"
            exception_cause, cause_confidence = "Potential Duplicate - Multiple records share the PO and/or Invoice", "Review"
        elif len(available) == 1 and minimum_difference == 0:
            reason = "Duplicate or ambiguous values - exact candidate is not uniquely one-to-one"
            exception_cause, cause_confidence = "Potential Duplicate - Exact candidate is not uniquely available", "Review"
        elif len(available) == 1 and minimum_difference is not None:
            reason = "Potential amount variance - reference agrees but amount differs"
            exception_cause, cause_confidence = "Potential Typo - Matching PO and/or Invoice values have different amounts", "Review"
        elif len(available) == 1:
            reason = "Reference candidate has an invalid or missing amount"
            exception_cause, cause_confidence = "Invalid Infinium candidate amount", "High"
        elif used:
            reason = _ALREADY_MATCHED_DISPOSITION
            exception_cause, cause_confidence = "Potential Duplicate - Reference already used by another match", "Review"
        else:
            reason = "No matching Infinium records"
            exception_cause, cause_confidence = "No corresponding Infinium record", "High"
        candidate_records.append(
            {
                "QuickBooks Row ID": qb.at[qidx, QB_ID],
                "Exception Cause": exception_cause,
                "Cause Confidence": cause_confidence,
                "Disposition": reason,
                "Total Candidate Count": len(candidate_indexes),
                "Available Candidate Count": len(available),
                "Available Infinium Candidate IDs": format_candidate_ids(available),
                "Already-Matched Candidate IDs": format_candidate_ids(used),
                "Minimum Amount Difference": cents_to_float(minimum_difference)
                if minimum_difference is not None else None,
                "Typo Candidate IDs": format_candidate_ids(typo_candidates),
            }
        )
    candidates = pd.DataFrame(candidate_records, columns=candidate_columns)
    return matches, sorted(remaining_q), sorted(remaining_i), candidates
