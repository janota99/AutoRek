"""Section, hold, disposition, and match-reference labels shared across the matching package."""

from __future__ import annotations




REFERENCE_HOLD_SECTION = "11 Review Hold QuickBooks"


HOLD_PO_ALREADY_REPRESENTED = "REVIEW_HOLD_PO_ALREADY_REPRESENTED"


HOLD_AMOUNT_VARIANCE = "REVIEW_HOLD_AMOUNT_VARIANCE"


HOLD_EXACT_CANDIDATE_NOT_UNIQUE = "REVIEW_HOLD_EXACT_CANDIDATE_NOT_UNIQUE"


HOLD_MULTIPLE_CANDIDATES = "REVIEW_HOLD_MULTIPLE_CANDIDATES"


HOLD_PO_REUSE = "REVIEW_HOLD_PO_REUSE"


HOLD_TYPO_CANDIDATES = "REVIEW_HOLD_TYPO_CANDIDATES"


HOLD_HISTORICAL_CLEARANCE = "REVIEW_HOLD_HISTORICAL_CLEARANCE"


HOLD_POTENTIAL_DUPLICATE = "REVIEW_HOLD_POTENTIAL_DUPLICATE"


HOLD_INVALID_AMOUNT = "REVIEW_HOLD_INVALID_AMOUNT"


HOLD_CANDIDATE_INVALID_AMOUNT = "REVIEW_HOLD_CANDIDATE_INVALID_AMOUNT"


REFERENCE_HOLD_COLUMNS = [
    "Hold ID", "Reason Code", "Classification", "Confidence",
    "QuickBooks Row ID", "QuickBooks Row Index", "Normalized PO", "Normalized Invoice",
    "QuickBooks Amount", "Infinium Amount", "Amount Difference",
    "Related Match Ref.", "Related QuickBooks Row IDs", "Related Infinium Row IDs",
    "Accrual Treatment", "Posting Disposition", "Explanation",
    "Manual Decision", "Reviewed By", "Review Timestamp", "Review Rationale",
]


DISPOSITION_MATCHED = "MATCHED"


DISPOSITION_DUPLICATE_EXCLUDED = "EXACT_QBO_DUPLICATE_EXCLUDED"


DISPOSITION_REVIEW_HOLD = "REVIEW_HOLD"


DISPOSITION_TRUE_UNMATCHED = "TRUE_UNMATCHED"


FINAL_DISPOSITIONS = (
    DISPOSITION_MATCHED,
    DISPOSITION_DUPLICATE_EXCLUDED,
    DISPOSITION_REVIEW_HOLD,
    DISPOSITION_TRUE_UNMATCHED,
)


QB_DISPOSITION_COLUMNS = [
    "QBO Row ID", "Normalized PO", "Normalized Invoice", "Amount",
    "Final Disposition", "Reason Code", "Final Reason",
    "Match Ref.", "Aggregate Group ID", "Duplicate Group ID", "Canonical QBO Row ID",
    "Review ID", "Related Match Ref.", "Related Infinium Row IDs",
    "In Proposed JE",
]


MATCH_REF_COLUMN = "Match Ref."


REFERENCED_MATCH_REF_COLUMN = "Referenced Match Ref."


MATCH_TYPE_ONE_TO_ONE = "One-to-One"


MATCH_TYPE_GROUPED = "Grouped"


MATCH_REGISTER_COLUMNS = [
    "Match Ref.", "Match Type", "Record Scope", "Match Basis",
    "QuickBooks Row Count", "Infinium Row Count",
    "QuickBooks Amount", "Infinium Amount", "Amount Difference",
    "QuickBooks Row IDs", "Infinium Row IDs", "Clearance ID",
]


_ALREADY_MATCHED_DISPOSITION = "Potential duplicate - referenced Infinium candidate is already matched"
