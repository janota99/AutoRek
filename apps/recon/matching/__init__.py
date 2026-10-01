"""Controlled QuickBooks-to-Infinium normalization and matching engine.

Automatic matching first exhausts unique one-to-one relationships. A bounded,
unambiguous one-to-many or many-to-one pass may then match rows sharing an exact
normalized PO and/or invoice when their signed-cent totals agree exactly.

Duplicate detection, disposition, and reporting are handled by
``duplicates.py``. Strong groups retain one canonical primary row and exclude
only excess copies. Weaker candidates remain visible for review. Historical
rows overlapping their own primary dataset are prevented from clearing an
exception.

The package re-exports its public names, so callers keep importing from
``matching`` directly. Submodules, in dependency order:

    core            versions, constants, result types, normalization primitives
    labels          section, hold, disposition, and match-reference labels
    engine          working-frame prep and ``perform_matching`` (every matching pass)
    exceptions      amount variances, ambiguous duplicates, PO reuse errors
    references      match reference IDs (assign, refine, validate)
    holds           reason codes and the reference-evidence review holds
    dispositions    final QuickBooks dispositions, fuzzy-match holds, historical clearances
    paired_rows     the side-by-side paired rows and per-match assessments
    summaries       method/product/customer summaries, exception analysis, controls
    validation      ``validate_reconciliation``: end-of-run integrity checks
    reconciliation  ``build_reconciliation``: runs every step above in order
"""

from __future__ import annotations

from .core import (
    APP_VERSION,
    cents_or_zero,
    cents_to_float,
    clean_alphanumeric,
    clean_po,
    flag_mask,
    get_fuzzy_lexicon_match,
    INF_ID,
    MatchGroup,
    MATCHING_RULE_VERSION,
    numeric_quantity_sum,
    numeric_sum,
    parse_amount_cents,
    parse_fiscal_period,
    PRIOR_PERIOD_URGENT_THRESHOLD,
    QB_ID,
    ReconciliationResult,
    valid_cents,
)
from .labels import (
    DISPOSITION_DUPLICATE_EXCLUDED,
    DISPOSITION_MATCHED,
    DISPOSITION_REVIEW_HOLD,
    DISPOSITION_TRUE_UNMATCHED,
    FINAL_DISPOSITIONS,
    MATCH_REF_COLUMN,
    QB_DISPOSITION_COLUMNS,
    REFERENCE_HOLD_COLUMNS,
    REFERENCE_HOLD_SECTION,
    REFERENCED_MATCH_REF_COLUMN,
)
from .engine import (
    perform_matching,
    prepare_working_frame,
)
from .exceptions import (
    AMBIGUOUS_DUPLICATE_COLUMNS,
    AMOUNT_VARIANCE_COLUMNS,
    build_ambiguous_duplicate_candidates,
    build_po_reuse_errors,
    build_reference_amount_variances,
    PO_REUSE_ERROR_COLUMNS,
    po_reuse_error_qb_index_map,
)
from .references import (
    add_duplicate_report_references,
    apply_referenced_match_references,
    assign_match_references,
    describe_match_references,
    refine_candidates_with_match_references,
    validate_match_references,
)
from .holds import (
    build_reference_evidence_review_holds,
    REASON_CODE_GLOSSARY,
    short_reason_code,
)
from .dispositions import (
    build_fuzzy_match_review_holds,
    build_qb_dispositions,
    FUZZY_MATCH_CLASSIFICATION_GROUPED,
    FUZZY_MATCH_CLASSIFICATION_SINGLE,
    HISTORICAL_CLEARANCE_COLUMNS,
)
from .summaries import (
    build_fiscal_exception_summary,
)
from .validation import (
    validate_reconciliation,
)
from .reconciliation import (
    build_reconciliation,
)
from ..duplicates import (
    AMOUNT_CENTS,
)

__all__ = [
    "AMBIGUOUS_DUPLICATE_COLUMNS",
    "AMOUNT_CENTS",
    "AMOUNT_VARIANCE_COLUMNS",
    "APP_VERSION",
    "INF_ID",
    "MATCH_REF_COLUMN",
    "MATCHING_RULE_VERSION",
    "PO_REUSE_ERROR_COLUMNS",
    "QB_ID",
    "DISPOSITION_DUPLICATE_EXCLUDED",
    "DISPOSITION_MATCHED",
    "DISPOSITION_REVIEW_HOLD",
    "DISPOSITION_TRUE_UNMATCHED",
    "FINAL_DISPOSITIONS",
    "QB_DISPOSITION_COLUMNS",
    "REASON_CODE_GLOSSARY",
    "REFERENCED_MATCH_REF_COLUMN",
    "REFERENCE_HOLD_COLUMNS",
    "REFERENCE_HOLD_SECTION",
    "short_reason_code",
    "ReconciliationResult",
    "add_duplicate_report_references",
    "apply_referenced_match_references",
    "assign_match_references",
    "describe_match_references",
    "build_ambiguous_duplicate_candidates",
    "build_fiscal_exception_summary",
    "build_qb_dispositions",
    "build_reference_evidence_review_holds",
    "build_po_reuse_errors",
    "build_reference_amount_variances",
    "build_reconciliation",
    "cents_or_zero",
    "cents_to_float",
    "clean_alphanumeric",
    "clean_po",
    "get_fuzzy_lexicon_match",
    "numeric_quantity_sum",
    "flag_mask",
    "numeric_sum",
    "parse_amount_cents",
    "parse_fiscal_period",
    "po_reuse_error_qb_index_map",
    "refine_candidates_with_match_references",
    "valid_cents",
    "validate_match_references",
]
