"""Shared evidence primitives: reference strength, link assessment, candidate search, corroboration.

Matching and exception classification must look at the same evidence. Every
function here works on the prepared working frames (see
``engine.prepare_working_frame``) and is read by both ``perform_matching`` (to
vet a proposed match) and the candidate table that explains every row that did
not match -- so a row can never be called "no matching records" by one step
while another step found eligible evidence.

Nothing here weakens a matching rule. It only adds reasons to withhold one:
a weak reference, or an identifier that points at a different transaction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import pandas as pd

from ..duplicates import AMOUNT_CENTS, NORM_INV, NORM_PO, SOURCE_POS
from .core import (
    clean_alphanumeric,
    clean_po,
    CORR_CUSTOMER,
    CORR_DATE,
    identifier_text,
    INF_ID,
    INV_FIELD_USED,
    KEY_INV,
    KEY_PO,
    PO_FIELD_USED,
    QB_ID,
    _RE_PO_PREFIX,
    _RE_TRAILING_ZEROS,
    SRC_INV,
    SRC_PO,
    cents_to_float,
    valid_cents,
)


# ---------------------------------------------------------------------------
# Evidence findings: one stable code per way a row can fail to match
# ---------------------------------------------------------------------------

FINDING_NO_EVIDENCE = "NO_EVIDENCE"
FINDING_WEAK_ONLY = "EVIDENCE_INSUFFICIENT_WEAK_REFERENCE"
FINDING_WEAK_EXACT_AMOUNT = "EVIDENCE_INSUFFICIENT_WEAK_REFERENCE_EXACT_AMOUNT"
FINDING_AMOUNT_DIFFERS = "EVIDENCE_FOUND_AMOUNT_DIFFERS"
FINDING_CONSUMED = "EVIDENCE_ALREADY_CONSUMED"
FINDING_MULTIPLE = "EVIDENCE_MULTIPLE_CANDIDATES"
FINDING_EXACT_NOT_UNIQUE = "EVIDENCE_EXACT_AMOUNT_NOT_UNIQUE"
FINDING_CONFLICTING_LINKS = "EVIDENCE_CONFLICTING_LINKS"
FINDING_TYPO_CANDIDATES = "EVIDENCE_TYPO_CANDIDATES"
FINDING_INVALID_AMOUNT = "EVIDENCE_INVALID_AMOUNT"

FINDING_EXPLANATIONS = {
    FINDING_NO_EVIDENCE: "No Infinium record shares this row's PO or invoice.",
    FINDING_WEAK_ONLY: (
        "Only a weak reference (a placeholder, generic word, or value with no digits) is shared with "
        "Infinium, and no Infinium amount agrees. Informational: a weak reference is not proof of the "
        "same transaction, and the row stays JE support."
    ),
    FINDING_WEAK_EXACT_AMOUNT: (
        "Only a weak reference is shared with an Infinium record, but that record's amount agrees to "
        "the cent. That is too much to ignore and too little to accept: the sale may already be "
        "recorded, so the row is held rather than accrued."
    ),
    FINDING_AMOUNT_DIFFERS: "A specific reference agrees with an Infinium record whose signed amount differs.",
    FINDING_CONSUMED: "Every Infinium record sharing a specific reference was already consumed by another match.",
    FINDING_MULTIPLE: "More than one unresolved Infinium record shares a specific reference.",
    FINDING_EXACT_NOT_UNIQUE: "An Infinium record agrees on reference and amount but the pairing is not uniquely one-to-one.",
    FINDING_CONFLICTING_LINKS: (
        "The row's PO and invoice point to different Infinium transactions (or the proposed Infinium "
        "record is linked to another QuickBooks row). Neither link is allowed to win by processing order."
    ),
    FINDING_TYPO_CANDIDATES: "More than one Infinium record fits the controlled-typo rule.",
    FINDING_INVALID_AMOUNT: "An amount on this row or its only candidate cannot be read as signed cents.",
}


# ---------------------------------------------------------------------------
# Reference strength
# ---------------------------------------------------------------------------

# Placeholders and generic words people type where a PO or invoice belongs.
# Matching them proves nothing: they are written on unrelated sales. Kept short
# and literal on purpose -- every entry is a decision to withhold a match, so
# the list is explicit rather than inferred.

WEAK_REFERENCE_WORDS = frozenset({
    "NA", "NONE", "NIL", "NULL", "TBD", "TBA", "TEST", "CASH", "STOCK", "SAME", "VARIOUS",
    "UNKNOWN", "NOPO", "MISC", "VERBAL", "PHONE", "EMAIL", "PICKUP", "WALKIN", "DEFAULT",
    "PENDING", "SEEPO", "SEENOTES", "SEEATTACHED", "ATTACHED", "NOPONUMBER", "NOREFERENCE",
})

STRENGTH_SPECIFIC = "SPECIFIC"
STRENGTH_WEAK = "WEAK"
STRENGTH_NONE = "NONE"

# A specific value that appears on this many rows of ONE source file is noted
# as heavily reused (informational: uniqueness rules, not this, decide matches).
HEAVY_REUSE_ROWS = 6


def reference_strength(normalized: str) -> str:
    """SPECIFIC, WEAK, or NONE (blank) for a normalized PO or invoice.

    Weak: a listed placeholder word; all zeros ("0", "0000"); or one character
    repeated four or more times ("XXXX"). A longer value with no digit
    (a name typed into the PO field) is deliberately NOT treated as weak here:
    the existing suite and the sample data use such references, and changing
    that would move accepted matches. It is recorded as a known issue for a
    decision instead."""
    if not normalized:
        return STRENGTH_NONE
    if normalized in WEAK_REFERENCE_WORDS:
        return STRENGTH_WEAK
    if set(normalized) == {"0"} or (len(normalized) >= 4 and len(set(normalized)) == 1):
        return STRENGTH_WEAK
    return STRENGTH_SPECIFIC


def specific_key(normalized: str) -> str:
    """The normalized value when it is specific evidence, else blank."""
    return normalized if reference_strength(normalized) == STRENGTH_SPECIFIC else ""


def ensure_key_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Add KEY_PO / KEY_INV when a frame was built without them."""
    if KEY_PO not in frame.columns:
        frame[KEY_PO] = frame[NORM_PO].map(specific_key)
    if KEY_INV not in frame.columns:
        frame[KEY_INV] = frame[NORM_INV].map(specific_key)
    return frame


_RE_DIGIT = re.compile(r"\d")


def extract_reference(text: Any, cleaner: Any = clean_po) -> tuple[str, str]:
    """A PO/invoice reference from a configured fallback column.

    A single token is normalized as a PO would be, but must still be at least
    four characters including a digit. Free text (a description)
    yields a reference only when it holds exactly one token that looks like an
    identifier -- at least four characters including a digit. Several such
    tokens are ambiguous and yield nothing, so a description can supply
    evidence but can never guess among references.

    Returns (normalized reference, how it was read)."""
    raw = identifier_text(text)
    if not raw:
        return "", ""
    if not re.search(r"\s", raw):
        single = cleaner(raw)
        # A fallback must look like an identifier even when it is a whole cell:
        # a word ("thanks") in a description column is not a reference.
        return (single, "column") if len(single) >= 4 and _RE_DIGIT.search(single) else ("", "")
    tokens = [clean_alphanumeric(token) for token in re.split(r"\s+", raw)]
    specific = list(dict.fromkeys(
        token for token in tokens if len(token) >= 4 and _RE_DIGIT.search(token)
    ))
    if len(specific) == 1:
        return specific[0], "description"
    return "", "ambiguous" if specific else ""


# ---------------------------------------------------------------------------
# Link index and link assessment
# ---------------------------------------------------------------------------

LINK_CLEAN = "CLEAN"
LINK_DISCREPANCY = "DISCREPANCY"
LINK_CONFLICT = "CONFLICT"

_KIND_LABEL = {"PO": "PO", "INV": "Invoice"}
_KIND_COLUMNS = {"PO": (KEY_PO, SRC_PO), "INV": (KEY_INV, SRC_INV)}


@dataclass
class LinkIndex:
    """Specific PO / invoice values -> the rows of one frame carrying them."""

    po: dict[str, set[int]] = field(default_factory=dict)
    inv: dict[str, set[int]] = field(default_factory=dict)

    @classmethod
    def from_frame(cls, frame: pd.DataFrame) -> "LinkIndex":
        ensure_key_columns(frame)
        index = cls()
        for idx, po, inv in zip(frame.index, frame[KEY_PO], frame[KEY_INV]):
            if po:
                index.po.setdefault(po, set()).add(int(idx))
            if inv:
                index.inv.setdefault(inv, set()).add(int(idx))
        return index

    def rows(self, kind: str, value: str) -> set[int]:
        return (self.po if kind == "PO" else self.inv).get(value, set())


@dataclass
class LinkAssessment:
    status: str = LINK_CLEAN
    detail: str = ""
    conflicting_inf_rows: set[int] = field(default_factory=set)
    conflicting_qb_rows: set[int] = field(default_factory=set)


def _source_text(frame: pd.DataFrame, rows: Iterable[int], column: str) -> str:
    values = list(dict.fromkeys(
        str(frame.at[int(r), column]) for r in rows
        if column in frame.columns and str(frame.at[int(r), column])
    ))
    return ", ".join(f"'{v}'" for v in values) if values else "(blank)"


def _label_in_sentence(label: str) -> str:
    return label if label.isupper() else label.lower()


def assess_group_links(
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    q_rows: Iterable[int],
    i_rows: Iterable[int],
    key_kinds: set[str],
    qb_links: LinkIndex,
    inf_links: LinkIndex,
) -> LinkAssessment:
    """Do the identifiers that were NOT the matching key agree, differ without
    consequence, or point at different transactions?

    For every kind (PO, invoice) not used as the key: when both sides carry a
    value and none is shared, the values differ. If a QuickBooks value is also
    carried by an Infinium row outside this match -- or an Infinium value by a
    QuickBooks row outside it -- the identifier points at another transaction:
    a CONFLICT, and the match is not accepted. Otherwise the difference
    competes with nothing and the match stands with the discrepancy recorded.

    The test reads the whole frames, never "what is still unmatched", so the
    verdict cannot depend on which pass ran first."""
    q_rows = [int(r) for r in q_rows]
    i_rows = [int(r) for r in i_rows]
    q_set, i_set = set(q_rows), set(i_rows)
    assessment = LinkAssessment()
    notes: list[str] = []
    for kind in ("PO", "INV"):
        if kind in key_kinds:
            continue
        key_col, src_col = _KIND_COLUMNS[kind]
        q_values = {qb.at[r, key_col] for r in q_rows if qb.at[r, key_col]}
        i_values = {inf.at[r, key_col] for r in i_rows if inf.at[r, key_col]}
        if not q_values or not i_values or q_values & i_values:
            continue
        elsewhere_inf = set().union(*(inf_links.rows(kind, v) for v in q_values)) - i_set
        elsewhere_qb = set().union(*(qb_links.rows(kind, v) for v in i_values)) - q_set
        label = _KIND_LABEL[kind]
        shown = (
            f"{label} differs: QuickBooks {_source_text(qb, q_rows, src_col)} vs "
            f"Infinium {_source_text(inf, i_rows, src_col)}"
        )
        if elsewhere_inf or elsewhere_qb:
            assessment.status = LINK_CONFLICT
            assessment.conflicting_inf_rows |= elsewhere_inf
            assessment.conflicting_qb_rows |= elsewhere_qb
            where = []
            if elsewhere_inf:
                where.append(
                    f"the QuickBooks {_label_in_sentence(label)} is also carried by Infinium "
                    + ", ".join(str(inf.at[r, INF_ID]) for r in sorted(elsewhere_inf))
                )
            if elsewhere_qb:
                where.append(
                    f"the Infinium {_label_in_sentence(label)} is also carried by QuickBooks "
                    + ", ".join(str(qb.at[r, QB_ID]) for r in sorted(elsewhere_qb))
                )
            notes.append(f"{shown}; " + " and ".join(where) + ".")
        else:
            if assessment.status != LINK_CONFLICT:
                assessment.status = LINK_DISCREPANCY
            notes.append(f"{shown} (not linked to any other record)")
    assessment.detail = "; ".join(notes)
    return assessment


def _folded_source(kind: str, text: Any) -> str:
    """Source text folded by the documented equivalences only -- case,
    whitespace, a trailing ".0", and (for a PO) the leading "PO" / "P.O."
    marker -- and nothing else, so punctuation survives for comparison."""
    folded = _RE_TRAILING_ZEROS.sub(r"\1", str(text).strip().upper())
    if kind == "PO":
        folded = _RE_PO_PREFIX.sub("", folded)
    return re.sub(r"\s+", "", folded)


def punctuation_discrepancies(
    qb: pd.DataFrame, inf: pd.DataFrame, q_rows: Iterable[int], i_rows: Iterable[int],
) -> str:
    """Key fields that agree after normalization but differ in the source text
    beyond the documented equivalences -- e.g. 'AB-12' vs 'AB12'. Normalization
    treats them as the same identifier (documented rule); the match records
    that it did. The ordinary "PO " prefix is a documented equivalence and is
    not reported."""
    notes: list[str] = []
    for kind, (key_col, src_col) in _KIND_COLUMNS.items():
        q_by_key: dict[str, set[str]] = {}
        i_by_key: dict[str, set[str]] = {}
        for r in q_rows:
            if qb.at[int(r), key_col]:
                q_by_key.setdefault(qb.at[int(r), key_col], set()).add(_folded_source(kind, qb.at[int(r), src_col]))
        for r in i_rows:
            if inf.at[int(r), key_col]:
                i_by_key.setdefault(inf.at[int(r), key_col], set()).add(_folded_source(kind, inf.at[int(r), src_col]))
        for key in set(q_by_key) & set(i_by_key):
            if not q_by_key[key] & i_by_key[key]:
                notes.append(
                    f"{_KIND_LABEL[kind]} equal only after ignoring punctuation: QuickBooks "
                    f"{sorted(q_by_key[key])} vs Infinium {sorted(i_by_key[key])}"
                )
    return "; ".join(notes)


# ---------------------------------------------------------------------------
# Corroboration (explanatory; never an acceptance rule on its own)
# ---------------------------------------------------------------------------

CORROBORATION_DATE_WINDOW_DAYS = 31

_RE_NAME_TOKEN = re.compile(r"[A-Z]{3,}")


def _name_tokens(text: str) -> set[str]:
    return set(_RE_NAME_TOKEN.findall(text.upper()))


def corroboration(
    qb: pd.DataFrame, inf: pd.DataFrame, q_rows: Iterable[int], i_rows: Iterable[int],
) -> str:
    """Plain-language account of what customer and date say about a candidate
    pair. QuickBooks names customers and Infinium numbers them, so a customer
    that cannot be compared is reported as such -- never as a contradiction.
    A date outside the window is an accounting-period difference, kept apart
    from transaction identity. Neither changes whether a match is accepted;
    identifier links (assess_group_links) do."""
    q_rows = [int(r) for r in q_rows]
    i_rows = [int(r) for r in i_rows]
    parts: list[str] = []
    if CORR_CUSTOMER in qb.columns and CORR_CUSTOMER in inf.columns:
        q_names = {qb.at[r, CORR_CUSTOMER] for r in q_rows if qb.at[r, CORR_CUSTOMER]}
        i_names = {inf.at[r, CORR_CUSTOMER] for r in i_rows if inf.at[r, CORR_CUSTOMER]}
        if not q_names or not i_names:
            parts.append("customer: not available on both sides")
        elif q_names & i_names or any(_name_tokens(a) & _name_tokens(b) for a in q_names for b in i_names):
            parts.append("customer: agrees")
        else:
            parts.append("customer: not comparable (different naming between systems)")
    if CORR_DATE in qb.columns and CORR_DATE in inf.columns:
        q_dates = [qb.at[r, CORR_DATE] for r in q_rows if not pd.isna(qb.at[r, CORR_DATE])]
        i_dates = [inf.at[r, CORR_DATE] for r in i_rows if not pd.isna(inf.at[r, CORR_DATE])]
        if q_dates and i_dates:
            gap = min(abs((a - b).days) for a in q_dates for b in i_dates)
            if gap <= CORROBORATION_DATE_WINDOW_DAYS:
                parts.append(f"date: within {CORROBORATION_DATE_WINDOW_DAYS} days ({gap}d)")
            else:
                parts.append(f"date: {gap} days apart (accounting-period difference, not identity evidence)")
        else:
            parts.append("date: not available on both sides")
    return "; ".join(parts)


def evidence_summary(
    qb: pd.DataFrame, inf: pd.DataFrame, q_rows: Iterable[int], i_rows: Iterable[int],
    key_kinds: set[str], discrepancy: str,
) -> str:
    """Which fields carried the match and where each came from."""
    q_rows = [int(r) for r in q_rows]
    i_rows = [int(r) for r in i_rows]
    fields: list[str] = []
    for kind in ("PO", "INV"):
        if kind not in key_kinds:
            continue
        field_col = PO_FIELD_USED if kind == "PO" else INV_FIELD_USED
        q_fields = sorted({str(qb.at[r, field_col]) for r in q_rows if field_col in qb.columns and qb.at[r, field_col]})
        i_fields = sorted({str(inf.at[r, field_col]) for r in i_rows if field_col in inf.columns and inf.at[r, field_col]})
        fields.append(
            f"{_KIND_LABEL[kind]} (QuickBooks {', '.join(q_fields) or 'mapped column'}; "
            f"Infinium {', '.join(i_fields) or 'mapped column'})"
        )
    text = "Matched on " + " + ".join(fields) + " and exact signed amount"
    if discrepancy:
        text += f". Non-competing discrepancy: {discrepancy}"
    context = corroboration(qb, inf, q_rows, i_rows)
    if context:
        text += f". Context: {context}"
    return text


# ---------------------------------------------------------------------------
# Candidate search -- the evidence behind every unmatched row
# ---------------------------------------------------------------------------


@dataclass
class CandidateSearch:
    """Everything the opposing frame holds for one QuickBooks row."""

    strong: dict[int, set[str]] = field(default_factory=dict)   # inf idx -> {"PO", "INVOICE"}
    weak: dict[int, set[str]] = field(default_factory=dict)     # inf idx -> weak kinds shared
    reused_note: str = ""


def search_candidates(
    qb: pd.DataFrame,
    qidx: int,
    inf_links: LinkIndex,
    inf_weak_po: dict[str, set[int]],
    inf_weak_inv: dict[str, set[int]],
) -> CandidateSearch:
    """Every Infinium row sharing a specific reference (strong) or only a weak
    one (weak) with this QuickBooks row, with the field(s) that linked it."""
    search = CandidateSearch()
    po, inv = qb.at[qidx, KEY_PO], qb.at[qidx, KEY_INV]
    if po:
        for r in inf_links.rows("PO", po):
            search.strong.setdefault(r, set()).add("PO")
    if inv:
        for r in inf_links.rows("INV", inv):
            search.strong.setdefault(r, set()).add("INVOICE")
    weak_po, weak_inv = qb.at[qidx, NORM_PO], qb.at[qidx, NORM_INV]
    if weak_po and not po:
        for r in inf_weak_po.get(weak_po, set()):
            search.weak.setdefault(r, set()).add("PO")
    if weak_inv and not inv:
        for r in inf_weak_inv.get(weak_inv, set()):
            search.weak.setdefault(r, set()).add("INVOICE")
    for r in list(search.weak):
        if r in search.strong:
            del search.weak[r]
    return search


def weak_value_index(frame: pd.DataFrame, norm_column: str, key_column: str) -> dict[str, set[int]]:
    """Weak (non-specific, non-blank) normalized values -> rows carrying them."""
    index: dict[str, set[int]] = {}
    for idx, norm, key in zip(frame.index, frame[norm_column], frame[key_column]):
        if norm and not key:
            index.setdefault(norm, set()).add(int(idx))
    return index


def describe_candidates(
    inf: pd.DataFrame, qb: pd.DataFrame, qidx: int,
    strong: dict[int, set[str]], weak: dict[int, set[str]], remaining_i: set[int],
    limit: int = 6,
) -> str:
    """One line per candidate: its ID, the fields that linked it, its amount
    and difference, its date, and whether another match already consumed it."""
    q_cents = qb.at[qidx, AMOUNT_CENTS]
    lines: list[str] = []
    ordered = sorted(
        [(r, kinds, "strong") for r, kinds in strong.items()]
        + [(r, kinds, "weak") for r, kinds in weak.items()],
        key=lambda item: (item[2] != "strong", int(inf.at[item[0], SOURCE_POS])),
    )
    for r, kinds, strength in ordered[:limit]:
        cents = inf.at[r, AMOUNT_CENTS]
        if valid_cents(cents) and valid_cents(q_cents):
            amount = f"{cents_to_float(cents):,.2f}"
            diff = int(q_cents) - int(cents)
            relation = "exact amount" if diff == 0 else f"differs by {cents_to_float(diff):,.2f}"
            amount_text = f"amount {amount} ({relation})"
        else:
            amount_text = "amount unreadable"
        date_text = ""
        if CORR_DATE in inf.columns and not pd.isna(inf.at[r, CORR_DATE]):
            date_text = f"; date {pd.Timestamp(inf.at[r, CORR_DATE]).date()}"
        state = "available" if r in remaining_i else "already consumed"
        lines.append(
            f"{inf.at[r, INF_ID]}: {strength} {'+'.join(sorted(kinds))}; {amount_text}{date_text}; {state}"
        )
    if len(ordered) > limit:
        lines.append(f"... ({len(ordered) - limit} more)")
    return " | ".join(lines)


def reuse_note(frame: pd.DataFrame, qidx: int) -> str:
    """Informational note when the row's specific PO/invoice is heavily reused
    inside its own file."""
    notes = []
    for label, column in (("PO", KEY_PO), ("Invoice", KEY_INV)):
        value = frame.at[qidx, column]
        if value and int((frame[column] == value).sum()) >= HEAVY_REUSE_ROWS:
            notes.append(f"{label} {value} appears on {int((frame[column] == value).sum())} rows")
    return "; ".join(notes)
