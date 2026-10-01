"""Versions, constants, result types, and normalization primitives shared by every matching step."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import get_close_matches
from functools import lru_cache
from typing import Any, Optional

import pandas as pd

from ..duplicates import AMOUNT_CENTS, IDENTITY_GRADE_ROLES, STABLE_FINGERPRINT_ROLES


APP_VERSION = "2.16.3"


MATCHING_RULE_VERSION = "2026.09-PACKAGING-WORDS-IGNORED"


# Grouped matching is intentionally bounded to keep reconciliation runs
# predictable. Larger or more complex reference pools remain unresolved for
# human review instead of risking a slow or arbitrary automatic allocation.

MAX_GROUP_POOL_ROWS = 20


MAX_GROUP_SIZE = 8


# A QuickBooks exception dated this many fiscal periods (or fewer) behind the
# selected reporting period is routine -- prior-period close activity is
# still normal at that distance. Only a wider gap escalates to "urgent".

PRIOR_PERIOD_URGENT_THRESHOLD = 2


QB_ID = "__REC_QB_ID"


INF_ID = "__REC_INF_ID"


PRODUCT_STANDARD = "__REC_PRODUCT_STANDARD"


FISCAL_LABEL = "__REC_FISCAL_LABEL"


# Every standard name ends in "<bottles per case> Case", and only 24, 32 and
# 40 exist (a test guards this). A variant without a size is listed only under
# a brand that comes in one size; a bare multi-size brand ("LOWES", "FOOD
# CLUB") is deliberately absent, so it goes to "Needs review" instead of
# guessing a pack size.

PRODUCT_LEXICON = {
    "Allsups 24 Case": ["ALLSUPS", "ALLSUPS 24", "ALLSUPS 24 CASE", "ALLSUP 24"],

    "Food Club 24 Case": [
        "FOOD CLUB 24", "FOOD CLUB 24 CASE", "FC 24",
        "FOODCLUB 24", "FOODCLUB24 CASE", "FC24 CASE",
    ],
    "Food Club 32 Case": [
        "FC32", "FC 32", "FC32 CASE", "FOOD CLUB 32", "FOOD CLUB 32 CASE",
        "FOODCLUB 32", "FOODCLUB32 CASE",
    ],
    "Food Club 40 Case": [
        "FOOD CLUB 40", "FOOD CLUB 40 CASE", "FC 40", "FOODCLUB 40",
        "FOODCLUB40 CASE", "FC40 CASE",
    ],  
    "Food King 24 Case": [
        "FOOD KING 24", "FOOD KING 24 CASE", "FK 24", "FOODKING 24",
        "FOODKING24 CASE", "FK24 CASE", "KINGS 24 CASE", "KINGS 24",
    ],
    "Food King 40 Case": [
        "FOOD KING 40", "FOOD KING 40 CASE", "FK 40", "FOODKING 40",
        "FOODKING40 CASE", "FK40 CASE", "KINGS 40 CASE", "KINGS 40",
    ],
    "Juniors 24 Case": ["JUNIORS", "JUNIORS 24", "JUNIORS 24 CASE"],
    "Lowes 24 Case": ["LOWES 24", "LOWES 24 CASE", "LOWES24"],
    "Lowes 32 Case": ["LOW 32", "LOW32", "LOWES 32", "LOWES 32 CASE"],
    "Lowes 40 Case": ["LOWES 40", "LOWES 40 CASE"],
    "Panhandle Pure 24 Case": [
        "PPL24", "PP 24 CASE", "PPL 24 CASSE", "PP24",
        "PANHANDLE PURE 24 CASE", "PPL 24 CASE", "PPL 24",
    ],
    "Panhandle Pure 32 Case": [
        "PP32", "PP 32", "PPL32", "PPL 32", "PP 32 CASE", "PANHANDLE PURE 32 CASE",
    ],
    "Panhandle Pure 40 Case": [
        "PPL40", "PP 40 CASE", "PPL 40 CASSE", "PP40",
        "PANHANDLE PURE 40 CASE", "PPL 40 CASE", "PPL 40",
    ],
    "Plains 24 Case": ["PLAINS", "PLAINS 24", "PLAINS 24 CASE", "PLAINS24"],
    "Toot N Totum 24 Case": [
        "TNT24", "TOOT N TOTUM", "TOOT 'N TOTUM 24", "TNT 24",
        "TOOT N TOTUM 24 CASE", "TOOTN TOTUM 24 CASE",
    ],
    "Spring House 24 Case": [
        "SPRING HOUSE 24", "SPRING HOUSE 24 CASE", "SH 24", "SPRINGHOUSE 24", "SPRINGHOUSE 24 CASE",
    ]
}


PACK_SIZES = (24, 32, 40)


# Labels for QuickBooks product lines the classifier can't place with
# certainty. They appear at the bottom of the Aggregates product table, in
# this order, with no bottle count -- never folded into a real product.

PRODUCT_SIZE_UNCLEAR = "Needs review – size unclear"


PRODUCT_NEW_32_COUNT = "New item – 32-count"


PRODUCT_UNRECOGNIZED = "Unrecognized product"


PRODUCT_REVIEW_LABELS = (PRODUCT_SIZE_UNCLEAR, PRODUCT_NEW_32_COUNT, PRODUCT_UNRECOGNIZED)


_RE_PACK_SIZE = re.compile(r"\s+(\d+)\s+CASE$", re.IGNORECASE)


# QuickBooks descriptions use CASE, PACK, their abbreviations and COUNT/CT
# interchangeably, or leave them off: "PP40", "PP 40 CASE" and "PP40PK" are
# the same product. The words carry no meaning, so they're removed before the
# lookup -- as whole words or stuck to a number, never inside another word
# ("PACKAGING" stays). A description that is only these words has no brand
# left and is "Unrecognized product".

_PACKAGING_WORDS = (
    "CASE", "CASES", "CS", "CSE", "PACK", "PACKS", "PK", "PKS", "PCK", "PKG", "COUNT", "CT",
)


_RE_LETTER_DIGIT_BOUNDARY = re.compile(r"(?<=\d)(?=[A-Z])|(?<=[A-Z])(?=\d)")


_RE_PACKAGING_WORD = re.compile(r"\b(?:" + "|".join(_PACKAGING_WORDS) + r")\b")


# The long words glued onto the end of a name ("ALLSUPSPACK"). Ordinary words
# end the same way (DISCOUNT, ACCOUNT, SHOWCASE), so the ending only comes off
# when what's left is a lexicon word or another packaging word ("CASEPACK"),
# and only once every other lookup has found nothing.

_RE_GLUED_PACKAGING_WORD = re.compile(r"\b([A-Z]+?)(?:CASES?|PACKS?|COUNT)\b")


_RE_SIZE_TOKEN = re.compile(r"\d+")


_RE_TRAILING_ZEROS = re.compile(r"^([0-9]+)\.0+$")


_RE_NON_ALNUM = re.compile(r"[^A-Z0-9]")


_RE_PO_PREFIX = re.compile(r"^P\.?\s*O\.?(?:\s*[#:\-]\s*|\s+|(?=\d))")


_RE_FISCAL_PERIOD = re.compile(r"(?:PD|P|PERIOD)?\s*(\d{1,2})(?:\.0+)?(?:\s*[-/]\s*(\d{4}|\d{2}))?")


@dataclass
class MatchGroup:
    qb_rows: list[int]
    inf_rows: list[int]
    method: str
    confidence: str
    explanation: str
    group_level: bool = False
    match_id: str = ""
    match_ref: str = ""


@dataclass
class ReconciliationResult:
    run_id: str
    run_timestamp: datetime
    qb_raw: pd.DataFrame
    inf_raw: pd.DataFrame
    qb_work: pd.DataFrame
    inf_work: pd.DataFrame
    matches: list[MatchGroup]
    paired_rows: list[dict[str, Any]]
    candidates: pd.DataFrame
    assessments: pd.DataFrame
    method_summary: pd.DataFrame
    exception_analysis: pd.DataFrame
    amount_variance_analysis: pd.DataFrame
    duplicate_analysis: pd.DataFrame
    infinium_duplicate_analysis: pd.DataFrame
    product_summary: pd.DataFrame
    customer_summary: pd.DataFrame
    controls: pd.DataFrame
    metrics: dict[str, Any]
    qb_mapping: dict[str, Optional[str]]
    inf_mapping: dict[str, Optional[str]]
    metadata: dict[str, Any]
    unmatched_qb: list[int] = field(default_factory=list)
    unmatched_inf: list[int] = field(default_factory=list)
    historical_clearances: pd.DataFrame = field(default_factory=pd.DataFrame)
    qb_secondary_raw: Optional[pd.DataFrame] = None
    inf_secondary_raw: Optional[pd.DataFrame] = None
    qb_secondary_work: Optional[pd.DataFrame] = None
    inf_secondary_work: Optional[pd.DataFrame] = None
    qb_secondary_mapping: Optional[dict[str, Optional[str]]] = None
    inf_secondary_mapping: Optional[dict[str, Optional[str]]] = None
    duplicate_qb_rows: list[int] = field(default_factory=list)
    duplicate_inf_rows: list[int] = field(default_factory=list)
    duplicate_qb_secondary_rows: list[int] = field(default_factory=list)
    duplicate_inf_secondary_rows: list[int] = field(default_factory=list)
    suspected_qb_rows: list[int] = field(default_factory=list)
    suspected_inf_rows: list[int] = field(default_factory=list)
    suspected_qb_secondary_rows: list[int] = field(default_factory=list)
    suspected_inf_secondary_rows: list[int] = field(default_factory=list)
    duplicate_review_hold_qb_rows: list[int] = field(default_factory=list)
    duplicate_review_hold_inf_rows: list[int] = field(default_factory=list)
    amount_variance_review_hold_qb_rows: list[int] = field(default_factory=list)
    amount_variance_review_hold_inf_rows: list[int] = field(default_factory=list)
    ambiguous_duplicate_analysis: pd.DataFrame = field(default_factory=pd.DataFrame)
    ambiguous_duplicate_qb_rows: list[int] = field(default_factory=list)
    po_reuse_errors: pd.DataFrame = field(default_factory=pd.DataFrame)
    match_register: pd.DataFrame = field(default_factory=pd.DataFrame)
    fuzzy_match_review_hold_analysis: pd.DataFrame = field(default_factory=pd.DataFrame)
    fuzzy_match_review_hold_qb_rows: list[int] = field(default_factory=list)
    fuzzy_match_review_hold_inf_rows: list[int] = field(default_factory=list)
    reference_hold_analysis: pd.DataFrame = field(default_factory=pd.DataFrame)
    reference_hold_qb_rows: list[int] = field(default_factory=list)
    qb_dispositions: pd.DataFrame = field(default_factory=pd.DataFrame)
    product_review_items: pd.DataFrame = field(default_factory=pd.DataFrame)
    customer_cases_without_bottles: dict[str, float] = field(default_factory=dict)


@lru_cache(maxsize=4096)
def _cached_clean_alphanumeric(text: str) -> str:
    text = _RE_TRAILING_ZEROS.sub(r"\1", text)
    return _RE_NON_ALNUM.sub("", text)


def clean_alphanumeric(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return _cached_clean_alphanumeric(str(value).strip().upper())


@lru_cache(maxsize=4096)
def _cached_clean_po(text: str) -> str:
    text = _RE_TRAILING_ZEROS.sub(r"\1", text)
    text = _RE_PO_PREFIX.sub("", text)
    return _RE_NON_ALNUM.sub("", text)


def clean_po(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return _cached_clean_po(str(value).strip().upper())


def parse_amount_cents(value: Any) -> Optional[int]:
    """Return exact signed cents, or None when the source amount is invalid."""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    negative_parentheses = text.startswith("(") and text.endswith(")")
    text = text.replace("$", "").replace(",", "").replace(" ", "")
    if negative_parentheses:
        text = "-" + text[1:-1]
    try:
        amount = Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if not amount.is_finite():
            return None
        return int(amount * 100)
    except (InvalidOperation, ValueError, TypeError):
        return None


def cents_to_float(value: Any) -> float:
    if value is None or pd.isna(value):
        return 0.0
    return float(Decimal(int(value)) / Decimal(100))


def cents_or_zero(value: Any) -> int:
    return 0 if value is None or pd.isna(value) else int(value)


def valid_cents(value: Any) -> bool:
    return value is not None and not pd.isna(value)


def _amount_total(frame: pd.DataFrame, indexes: Any) -> int:
    """Return an exact signed-cent total for the selected working rows."""
    return sum(
        cents_or_zero(frame.at[int(idx), AMOUNT_CENTS])
        for idx in indexes
    )


def _gross_amount_total(frame: pd.DataFrame, indexes: Any) -> int:
    """Return an exact absolute-cent total for the selected working rows."""
    return sum(
        abs(cents_or_zero(frame.at[int(idx), AMOUNT_CENTS]))
        for idx in indexes
    )


def _matched_row_indexes(
    matches: list[MatchGroup],
    dataset: str,
) -> list[int]:
    """Return unique primary row indexes consumed by accepted match groups."""
    attribute = "qb_rows" if dataset == "QB" else "inf_rows"
    return sorted(
        {
            int(idx)
            for group in matches
            for idx in getattr(group, attribute)
        }
    )


def _historical_row_indexes(
    clearances: pd.DataFrame,
    primary_dataset: str,
    index_column: str,
) -> list[int]:
    """Return unique populated indexes from one historical-clearance side."""
    if clearances.empty:
        return []
    values = clearances.loc[
        clearances["Primary Dataset"].eq(primary_dataset)
        & clearances[index_column].notna(),
        index_column,
    ]
    return sorted({int(value) for value in values})


def numeric_sum(series: pd.Series) -> float:
    return cents_to_float(sum(int(c) for c in series.map(parse_amount_cents) if valid_cents(c)))


def numeric_quantity_sum(series: pd.Series) -> float:
    return float(pd.to_numeric(series, errors="coerce").fillna(0).sum())


def flag_mask(series: pd.Series) -> pd.Series:
    """Boolean mask from a flag column, with blanks read as False.

    Same result as ``series.fillna(False).astype(bool)`` without relying on
    pandas' deprecated silent downcasting of object columns."""
    return series.map(lambda value: False if pd.isna(value) else bool(value)).astype(bool)


def _strip_packaging_words(text: str) -> str:
    """Upper-case text with the packaging words removed ("PP40PK" -> "PP 40 ")."""
    return _RE_PACKAGING_WORD.sub(" ", _RE_LETTER_DIGIT_BOUNDARY.sub(" ", text))


@lru_cache(maxsize=1)
def _glued_word_hosts() -> frozenset[str]:
    """The words a glued packaging word may be peeled off: every word in the
    lexicon names, plus the packaging words themselves."""
    words = set(_PACKAGING_WORDS)
    for standard_name, variants in PRODUCT_LEXICON.items():
        for name in (standard_name, *variants):
            words.update(re.findall(r"[A-Z]+", name.upper()))
    return frozenset(words)


def _unglue_packaging_words(text: str) -> str:
    """Stripped text with glued endings peeled off ("ALLSUPSPACK" -> "ALLSUPS")."""
    hosts = _glued_word_hosts()
    return _RE_GLUED_PACKAGING_WORD.sub(
        lambda found: found.group(1) if found.group(1) in hosts else found.group(0),
        _strip_packaging_words(text),
    )


@lru_cache(maxsize=1)
def _product_lookup() -> dict[str, str]:
    """Every lexicon name, as written and with packaging words stripped.

    The stripped keys give exact hits on stripped text; the originals keep
    near-spellings of a packaging word ("PPL 24 CASSE") close enough for
    get_close_matches, as they were before stripping existed."""
    lookup: dict[str, str] = {}
    for standard_name, variants in PRODUCT_LEXICON.items():
        for name in (standard_name, *variants):
            upper = name.upper()
            lookup[_cached_clean_alphanumeric(upper)] = standard_name
            lookup[_cached_clean_alphanumeric(_strip_packaging_words(upper))] = standard_name
    return lookup


def pack_size(standard_name: Any) -> Optional[int]:
    """Bottles per case from a standard product name ("Lowes 32 Case" -> 32),
    or None for a review label or anything without an explicit 24, 32 or 40."""
    match = _RE_PACK_SIZE.search(str(standard_name or ""))
    size = int(match.group(1)) if match else None
    return size if size in PACK_SIZES else None


def _product_brand(standard_name: str) -> str:
    return _RE_PACK_SIZE.sub("", standard_name)


@lru_cache(maxsize=1)
def _brand_pack_sizes() -> dict[str, frozenset[int]]:
    sizes: dict[str, set[int]] = {}
    for standard_name in PRODUCT_LEXICON:
        sizes.setdefault(_product_brand(standard_name), set()).add(pack_size(standard_name))
    return {brand: frozenset(values) for brand, values in sizes.items()}


def _lookup_product(lookup: dict[str, str], keys: tuple[str, ...]) -> Optional[str]:
    """Exact hit on any key, else the closest name to each key in turn."""
    keys = tuple(dict.fromkeys(keys))
    product = next((lookup[key] for key in keys if key in lookup), None)
    for key in keys:
        if product is not None:
            break
        close = get_close_matches(key, lookup.keys(), n=1, cutoff=0.82)
        product = lookup[close[0]] if close else None
    return product


@lru_cache(maxsize=1024)
def _cached_classify_product(text: str) -> str:
    """Name the product, then check the name against the size in the text.

    The lexicon lookup (exact, else get_close_matches at 0.82) only finds the
    nearest product name -- it can't tell "LOWES 40" from "LOWES 24" reliably,
    so the size written in the text decides:

    - text says 32 but the product isn't a 32 -> "New item – 32-count";
    - no product at all -> "Unrecognized product";
    - text gives a size that differs from the product's -> "Needs review";
    - text gives no size: a one-size brand (Allsups, Juniors, Plains,
      Toot N Totum, Spring House) takes that size; a multi-size brand
      (Lowes, Panhandle Pure, Food Club, Food King) -> "Needs review".

    Packaging words (CASE, PACK, PK, CT, ...) are stripped first, so they
    never decide the product on their own.
    """
    cleaned = _cached_clean_alphanumeric(_strip_packaging_words(text))
    if not cleaned:
        return PRODUCT_UNRECOGNIZED
    # The text as written is tried after the stripped text, so stripping only
    # ever adds a match: "PP 24 CASE PAK" is too short for get_close_matches
    # once CASE is gone, but still close to "PP 24 CASE". Glued words come
    # off only after both have found nothing, exact or close.
    lookup = _product_lookup()
    product = _lookup_product(lookup, (cleaned, _cached_clean_alphanumeric(text)))
    if product is None:
        unglued = _cached_clean_alphanumeric(_unglue_packaging_words(text))
        if unglued and unglued != cleaned:
            product = _lookup_product(lookup, (unglued,))
    product_size = pack_size(product) if product else None
    text_sizes = {int(token) for token in _RE_SIZE_TOKEN.findall(text)}
    if 32 in text_sizes and product_size != 32:
        return PRODUCT_NEW_32_COUNT
    if product is None:
        return PRODUCT_UNRECOGNIZED
    if text_sizes:
        return product if text_sizes == {product_size} else PRODUCT_SIZE_UNCLEAR
    return product if len(_brand_pack_sizes()[_product_brand(product)]) == 1 else PRODUCT_SIZE_UNCLEAR


def product_match(value: Any) -> str:
    """Classify a QuickBooks product description for reporting only -- never
    a financial match. Returns a PRODUCT_LEXICON name or one of
    PRODUCT_REVIEW_LABELS; a blank description is "Unrecognized product"."""
    if value is None or pd.isna(value):
        return PRODUCT_UNRECOGNIZED
    return _cached_classify_product(str(value).strip().upper())


def get_fuzzy_lexicon_match(value: Any) -> Optional[str]:
    """The PRODUCT_LEXICON name for a description, or None when it needs review."""
    label = product_match(value)
    return None if label in PRODUCT_REVIEW_LABELS else label


@lru_cache(maxsize=1024)
def _cached_parse_fiscal_period(text: str, default_year: int) -> tuple[Optional[int], Optional[int]]:
    match = _RE_FISCAL_PERIOD.fullmatch(text)
    if not match:
        return None, None
    period = int(match.group(1))
    year_text = match.group(2)
    year = int(year_text) if year_text else int(default_year)
    if year_text and len(year_text) == 2:
        year += 2000
    if not 1 <= period <= 13 or not 1900 <= year <= 2199:
        return None, None
    return period, year


def parse_fiscal_period(value: Any, default_year: int) -> tuple[Optional[int], Optional[int]]:
    if value is None or pd.isna(value):
        return None, None
    return _cached_parse_fiscal_period(str(value).strip().upper(), default_year)


def fiscal_label(value: Any, default_year: int) -> str:
    period, year = parse_fiscal_period(value, default_year)
    return f"P{period:02d}-{year}" if period is not None else "Unspecified"


def _fingerprint_columns(mapping: Optional[dict[str, Optional[str]]]) -> tuple[str, ...]:
    """The source columns that form the duplicate fingerprint: only the mapped
    stable line-level transaction attributes (customer, date, item, quantity,
    rate) -- an explicit, fixed set, never "all the other columns", and never
    the fiscal period."""
    if not mapping:
        return ()
    return tuple(mapping[role] for role in STABLE_FINGERPRINT_ROLES if mapping.get(role))


def _identity_columns(mapping: Optional[dict[str, Optional[str]]]) -> tuple[str, ...]:
    """The mapped subset of the fingerprint that distinguishes one line from
    another (customer, date, item, rate); quantity alone never counts."""
    if not mapping:
        return ()
    return tuple(mapping[role] for role in IDENTITY_GRADE_ROLES if mapping.get(role))


def _optional_index(value: Any) -> Optional[int]:
    return None if value is None or pd.isna(value) else int(value)


def _split_reference_ids(text: Any) -> list[str]:
    """Row IDs from a "; "-joined cell, ignoring blanks and the "... (N more)"
    tail format_candidate_ids appends when it truncates a long list."""
    if text is None or (not isinstance(text, str) and pd.isna(text)):
        return []
    return [
        piece.strip() for piece in str(text).split(";")
        if piece.strip() and not piece.strip().startswith("...")
    ]
