"""Reviewer decisions, the adjustment bridge, and approval -- layered on top of an immutable engine result.

The engine's classifications (``qb_dispositions``, ``matches``, the hold
reports) are never edited. A reviewer's decision is a separate, dated, attributed
record. ``apply_review_decisions`` validates the decisions against the engine
result, and returns a copy of it carrying:

  * ``review_adjustments`` -- one row per decision, with the engine's own
    classification beside the reviewer's action and its journal-entry effect;
  * ``adjustment_bridge`` -- Engine Proposed JE -> reviewer adjustments ->
    Reviewer-Adjusted JE, plus the open and non-posting items;
  * ``approval`` -- NOT APPROVED until ``record_approval`` is called.

Decisions are applied in the app and the workbook is regenerated; the workbook
then carries them in its Reviewer Disposition columns. The two Posting Summary
adjustments that depend on those columns (Release to JE, Exclude) are live
Excel formulas, so editing the dropdowns inside Excel recalculates them -- but
nothing in Excel re-validates record consumption or exact-cent agreement.
That happens only here.

Rules enforced:
  * every decision names a reviewer, a date, and a reason;
  * RELEASE_TO_JE, CONFIRM_MATCH and CARRY_FORWARD apply only to REVIEW_HOLD
    rows; EXCLUDE applies to TRUE_UNMATCHED rows (a JE removal) or to a hold
    (no JE effect) and always needs a supporting reference;
  * CONFIRM_MATCH names the Infinium rows it matches. Each must be a primary
    Infinium row that no accepted match or historical clearance already
    consumed, and no row may be consumed by two decisions. Exact signed-cent
    agreement is reported; a confirmed match whose amounts differ is recorded
    as a confirmed difference and is never posted.
  * one decision per QuickBooks row.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Optional

import pandas as pd

from ..duplicates import AMOUNT_CENTS
from .core import (
    _historical_row_indexes,
    cents_to_float,
    INF_ID,
    QB_ID,
    ReconciliationResult,
)
from .labels import DISPOSITION_REVIEW_HOLD, DISPOSITION_TRUE_UNMATCHED


ACTION_CONFIRM_MATCH = "CONFIRM_MATCH"
ACTION_RELEASE_TO_JE = "RELEASE_TO_JE"
ACTION_EXCLUDE = "EXCLUDE"
ACTION_CARRY_FORWARD = "CARRY_FORWARD"

REVIEW_ACTIONS = (
    ACTION_CONFIRM_MATCH, ACTION_RELEASE_TO_JE, ACTION_EXCLUDE, ACTION_CARRY_FORWARD,
)

# The label the workbook's Reviewer Disposition dropdown uses for each action.
WORKBOOK_DISPOSITION = {
    ACTION_CONFIRM_MATCH: "Confirm Match",
    ACTION_RELEASE_TO_JE: "Release to JE",
    ACTION_EXCLUDE: "Exclude",
    ACTION_CARRY_FORWARD: "Carry Forward",
}

APPROVAL_NOT_APPROVED = "NOT APPROVED"
APPROVAL_APPROVED = "APPROVED"

ADJUSTMENT_COLUMNS = [
    "Adjustment ID", "QuickBooks Row ID", "Engine Final Disposition", "Engine Reason Code",
    "Engine Classification Code", "QuickBooks Amount", "Reviewer Action", "Infinium Row IDs",
    "Infinium Amount", "Confirmed Amount Difference", "JE Effect", "Support Reference",
    "Reason", "Reviewer", "Decision Date", "Status",
]

BRIDGE_COLUMNS = ["Line", "Rows", "Amount", "Kind"]


class ReviewDecisionError(ValueError):
    """One or more reviewer decisions cannot be applied. ``problems`` lists each."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("Reviewer decisions rejected: " + " | ".join(problems))


@dataclass(frozen=True)
class ReviewDecision:
    qb_row_id: str
    action: str
    reviewer: str
    decided_on: Any
    reason: str
    support_reference: str = ""
    inf_row_ids: tuple[str, ...] = ()


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    try:
        return pd.Timestamp(str(value)).date()
    except (ValueError, TypeError):
        return None


def _cents(amount: Any) -> int:
    return 0 if amount is None or pd.isna(amount) else int(round(float(amount) * 100))


def consumed_infinium_ids(result: ReconciliationResult) -> set[str]:
    """Primary Infinium row IDs already consumed by an accepted match (one-to-one
    or grouped) or a historical clearance -- the set a manual match may not use."""
    consumed = {
        str(result.inf_work.at[idx, INF_ID])
        for group in result.matches for idx in group.inf_rows
    }
    consumed.update(
        str(result.inf_work.at[idx, INF_ID])
        for idx in _historical_row_indexes(result.historical_clearances, "Infinium Primary", "Primary Row Index")
    )
    # A fuzzy group is a review hold, not a consumption, but its Infinium rows are
    # spoken for until a reviewer resolves it, so a second manual claim is refused.
    for text in result.fuzzy_match_review_hold_analysis.get("Infinium Row IDs", []):
        consumed.update(piece.strip() for piece in str(text).split(";") if piece.strip())
    return consumed


def validate_review_decisions(
    result: ReconciliationResult, decisions: Iterable[ReviewDecision],
) -> list[str]:
    """Every problem with the decisions, empty when they can all be applied."""
    problems: list[str] = []
    ledger = result.qb_dispositions.set_index("QBO Row ID")
    inf_cents = dict(zip(result.inf_work[INF_ID], result.inf_work[AMOUNT_CENTS]))
    consumed = consumed_infinium_ids(result)
    seen_rows: set[str] = set()
    claimed_inf: dict[str, str] = {}
    for decision in decisions:
        tag = f"{decision.qb_row_id}"
        if decision.action not in REVIEW_ACTIONS:
            problems.append(f"{tag}: unknown action {decision.action!r}.")
            continue
        if decision.qb_row_id not in ledger.index:
            problems.append(f"{tag}: not a primary QuickBooks row in this run.")
            continue
        if decision.qb_row_id in seen_rows:
            problems.append(f"{tag}: more than one decision for the same row.")
        seen_rows.add(decision.qb_row_id)
        if not str(decision.reviewer or "").strip():
            problems.append(f"{tag}: reviewer name is required.")
        if _as_date(decision.decided_on) is None:
            problems.append(f"{tag}: a valid decision date is required.")
        if not str(decision.reason or "").strip():
            problems.append(f"{tag}: a reason is required.")
        engine = ledger.at[decision.qb_row_id, "Final Disposition"]
        if decision.action in (ACTION_RELEASE_TO_JE, ACTION_CONFIRM_MATCH, ACTION_CARRY_FORWARD):
            if engine != DISPOSITION_REVIEW_HOLD:
                problems.append(
                    f"{tag}: {decision.action} applies only to review holds (engine disposition is {engine})."
                )
        if decision.action == ACTION_EXCLUDE:
            if engine not in (DISPOSITION_TRUE_UNMATCHED, DISPOSITION_REVIEW_HOLD):
                problems.append(f"{tag}: EXCLUDE applies only to JE support or review-hold rows (engine disposition is {engine}).")
            if not str(decision.support_reference or "").strip():
                problems.append(f"{tag}: EXCLUDE requires a supporting reference (documentation).")
        if decision.action == ACTION_CONFIRM_MATCH:
            if not decision.inf_row_ids:
                problems.append(f"{tag}: CONFIRM_MATCH must name the Infinium row(s) it matches.")
            for inf_id in decision.inf_row_ids:
                if inf_id not in inf_cents:
                    problems.append(f"{tag}: {inf_id} is not a primary Infinium row in this run.")
                elif inf_id in consumed:
                    problems.append(
                        f"{tag}: {inf_id} is already consumed by an accepted match or historical clearance."
                    )
                elif inf_id in claimed_inf and claimed_inf[inf_id] != decision.qb_row_id:
                    problems.append(f"{tag}: {inf_id} is already claimed by the decision on {claimed_inf[inf_id]}.")
                else:
                    claimed_inf[inf_id] = decision.qb_row_id
    return problems


def _bridge(result: ReconciliationResult, adjustments: pd.DataFrame) -> pd.DataFrame:
    engine_cents = _cents(result.metrics["Proposed JE Amount"])
    released = adjustments.loc[adjustments["Reviewer Action"] == ACTION_RELEASE_TO_JE, "JE Effect"].map(_cents).sum()
    excluded = adjustments.loc[adjustments["Reviewer Action"] == ACTION_EXCLUDE, "JE Effect"].map(_cents).sum()
    adjusted_cents = engine_cents + int(released) + int(excluded)

    def count(action: str) -> int:
        return int((adjustments["Reviewer Action"] == action).sum())

    def amount(action: str) -> float:
        return cents_to_float(adjustments.loc[adjustments["Reviewer Action"] == action, "QuickBooks Amount"].map(_cents).sum())

    decided_ids = set(adjustments["QuickBooks Row ID"])
    holds = result.qb_dispositions.loc[result.qb_dispositions["Final Disposition"] == DISPOSITION_REVIEW_HOLD]
    undecided = holds.loc[~holds["QBO Row ID"].isin(decided_ids)]
    carried = adjustments.loc[adjustments["Reviewer Action"] == ACTION_CARRY_FORWARD]
    open_cents = int(undecided["Amount"].map(_cents).sum()) + int(carried["QuickBooks Amount"].map(_cents).sum())
    diff_cents = int(
        adjustments.loc[adjustments["Reviewer Action"] == ACTION_CONFIRM_MATCH, "Confirmed Amount Difference"].map(_cents).sum()
    )
    lines = [
        ("Engine Proposed JE (TRUE_UNMATCHED total; never changed)", int(result.metrics["Final Disposition - True Unmatched Rows"]), cents_to_float(engine_cents), "engine"),
        ("Plus: review holds released to JE", count(ACTION_RELEASE_TO_JE), cents_to_float(int(released)), "adjustment"),
        ("Less: JE support excluded with documentation", count(ACTION_EXCLUDE), cents_to_float(int(excluded)), "adjustment"),
        ("REVIEWER-ADJUSTED JE (calculated; not an approval)", None, cents_to_float(adjusted_cents), "result"),
        ("Memo: review holds confirmed as matched (no JE effect)", count(ACTION_CONFIRM_MATCH), amount(ACTION_CONFIRM_MATCH), "memo"),
        ("Memo: confirmed matches whose amounts differ (difference not posted)", int(
            (adjustments.loc[adjustments["Reviewer Action"] == ACTION_CONFIRM_MATCH, "Confirmed Amount Difference"].map(_cents) != 0).sum()
        ), cents_to_float(diff_cents), "memo"),
        ("Open: review holds undecided or carried forward", len(undecided) + count(ACTION_CARRY_FORWARD), cents_to_float(open_cents), "open"),
    ]
    bridge = pd.DataFrame(lines, columns=BRIDGE_COLUMNS)
    return bridge


def apply_review_decisions(
    result: ReconciliationResult, decisions: Iterable[ReviewDecision],
) -> ReconciliationResult:
    """A copy of ``result`` carrying the reviewer layer. The engine fields are
    shared, not modified; calling this again starts from the engine result."""
    decisions = list(decisions)
    problems = validate_review_decisions(result, decisions)
    if problems:
        raise ReviewDecisionError(problems)
    ledger = result.qb_dispositions.set_index("QBO Row ID")
    inf_cents = dict(zip(result.inf_work[INF_ID], result.inf_work[AMOUNT_CENTS]))
    records: list[dict[str, Any]] = []
    for number, decision in enumerate(decisions, 1):
        row = ledger.loc[decision.qb_row_id]
        q_cents = _cents(row["Amount"])
        inf_total = (
            sum(int(inf_cents[i]) for i in decision.inf_row_ids if not pd.isna(inf_cents[i]))
            if decision.action == ACTION_CONFIRM_MATCH else None
        )
        if decision.action == ACTION_RELEASE_TO_JE:
            je_effect = q_cents
        elif decision.action == ACTION_EXCLUDE and row["Final Disposition"] == DISPOSITION_TRUE_UNMATCHED:
            je_effect = -q_cents
        else:
            je_effect = 0
        records.append({
            "Adjustment ID": f"ADJ-{number:04d}",
            "QuickBooks Row ID": decision.qb_row_id,
            "Engine Final Disposition": row["Final Disposition"],
            "Engine Reason Code": row["Reason Code"],
            "Engine Classification Code": row.get("Classification Code", ""),
            "QuickBooks Amount": cents_to_float(q_cents),
            "Reviewer Action": decision.action,
            "Infinium Row IDs": "; ".join(decision.inf_row_ids),
            "Infinium Amount": cents_to_float(inf_total) if inf_total is not None else None,
            "Confirmed Amount Difference": cents_to_float(q_cents - inf_total) if inf_total is not None else None,
            "JE Effect": cents_to_float(je_effect),
            "Support Reference": str(decision.support_reference or "").strip(),
            "Reason": str(decision.reason).strip(),
            "Reviewer": str(decision.reviewer).strip(),
            "Decision Date": str(_as_date(decision.decided_on)),
            "Status": "Applied in app",
        })
    adjustments = pd.DataFrame(records, columns=ADJUSTMENT_COLUMNS)
    adjusted = dataclasses.replace(
        result,
        review_adjustments=adjustments,
        adjustment_bridge=_bridge(result, adjustments),
        approval={"status": APPROVAL_NOT_APPROVED},
    )
    return adjusted


def adjusted_je_amount(result: ReconciliationResult) -> float:
    """The reviewer-adjusted JE (engine total when there are no adjustments)."""
    if result.adjustment_bridge.empty:
        return float(result.metrics["Proposed JE Amount"])
    row = result.adjustment_bridge.loc[result.adjustment_bridge["Kind"] == "result"].iloc[0]
    return float(row["Amount"])


def approval_blockers(result: ReconciliationResult) -> list[str]:
    """Why this run cannot be approved yet; empty when it can."""
    blockers: list[str] = []
    decided = set(result.review_adjustments["QuickBooks Row ID"]) if not result.review_adjustments.empty else set()
    carried = set(
        result.review_adjustments.loc[
            result.review_adjustments["Reviewer Action"] == ACTION_CARRY_FORWARD, "QuickBooks Row ID",
        ]
    ) if not result.review_adjustments.empty else set()
    holds = result.qb_dispositions.loc[result.qb_dispositions["Final Disposition"] == DISPOSITION_REVIEW_HOLD, "QBO Row ID"]
    open_rows = sorted((set(holds) - decided) | (set(holds) & carried))
    if open_rows:
        blockers.append(f"{len(open_rows)} review hold(s) have no resolving decision: {', '.join(open_rows[:8])}"
                        + (" ..." if len(open_rows) > 8 else ""))
    if result.metrics.get("Invalid QuickBooks Amounts") or result.metrics.get("Invalid Infinium Amounts"):
        blockers.append("source rows with unreadable amounts remain")
    if result.duplicate_review_hold_inf_rows:
        blockers.append("Infinium duplicate review holds remain (resolve in the source data and rerun)")
    if result.suspected_qb_secondary_rows or result.suspected_inf_secondary_rows:
        blockers.append("historical duplicate review holds remain (resolve in the source data and rerun)")
    return blockers


def record_approval(
    result: ReconciliationResult, approver: str, approved_on: Any, note: str = "",
) -> ReconciliationResult:
    """Record an explicit approval of the reviewer-adjusted JE. Refused while
    anything remains open. The approval stores the amount it approved, so a
    later workbook edit that changes the live total is visibly different."""
    if not str(approver or "").strip():
        raise ReviewDecisionError(["approver name is required."])
    if _as_date(approved_on) is None:
        raise ReviewDecisionError(["a valid approval date is required."])
    blockers = approval_blockers(result)
    if blockers:
        raise ReviewDecisionError(blockers)
    return dataclasses.replace(
        result,
        approval={
            "status": APPROVAL_APPROVED,
            "approved_by": str(approver).strip(),
            "approved_on": str(_as_date(approved_on)),
            "approved_amount": adjusted_je_amount(result),
            "run_id": result.run_id,
            "note": str(note or "").strip(),
        },
    )


def approval_status_text(result: ReconciliationResult) -> str:
    approval = result.approval or {}
    if approval.get("status") == APPROVAL_APPROVED:
        return (
            f"APPROVED by {approval['approved_by']} on {approval['approved_on']} for "
            f"${approval['approved_amount']:,.2f} (run {approval['run_id']})"
        )
    return "NOT APPROVED -- no approval has been recorded for this run"


def decisions_from_frame(frame: pd.DataFrame) -> list[ReviewDecision]:
    """Build decisions from a table (an editor, CSV, or the adjustments sheet).
    Columns: QuickBooks Row ID, Reviewer Action, Reviewer, Decision Date, Reason,
    Support Reference, Infinium Row IDs ("; "-separated)."""
    decisions = []
    for record in frame.to_dict("records"):
        action = str(record.get("Reviewer Action") or "").strip()
        if not action and not str(record.get("QuickBooks Row ID") or "").strip():
            continue
        ids = tuple(
            piece.strip() for piece in str(record.get("Infinium Row IDs") or "").split(";") if piece.strip()
        )
        decisions.append(ReviewDecision(
            qb_row_id=str(record.get("QuickBooks Row ID") or "").strip(),
            action=action,
            reviewer=str(record.get("Reviewer") or ""),
            decided_on=record.get("Decision Date"),
            reason=str(record.get("Reason") or ""),
            support_reference=str(record.get("Support Reference") or ""),
            inf_row_ids=ids,
        ))
    return decisions
