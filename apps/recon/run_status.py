"""Workflow wording for the standard reconciliation page: what blocks the next step, and what changed.

Display-side only. Every figure comes from the run's own ``metrics`` or from labels the page
already holds; nothing here matches a row, classifies an exception, or touches a workbook.
Streamlit-free so it can be tested with plain dictionaries.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

# The figures worth comparing between two runs, in reading order.
SUMMARY_LABELS = {
    "Matched QuickBooks Rows": "Matched QuickBooks rows",
    "Unresolved QuickBooks Rows": "Unresolved QuickBooks rows",
    "Unmatched Infinium Rows": "Unmatched Infinium rows",
    "Final Disposition - Review Hold Rows": "Rows on review hold",
    "Proposed JE Amount": "Proposed journal entry",
    "Matched Amount Difference": "Matched amount difference",
    "Control Status": "Automated controls",
}
MONEY_KEYS = ("Proposed JE Amount", "Matched Amount Difference")


def summarize_run(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Copy the comparable figures out of a run's metrics (missing ones are left out)."""
    return {key: metrics[key] for key in SUMMARY_LABELS if key in metrics}


def upload_blockers(qb_loaded: bool, inf_loaded: bool) -> list[str]:
    """What is still needed before anything can run. The two uploads are independent."""
    out = []
    if not qb_loaded:
        out.append("Upload the QuickBooks sales file.")
    if not inf_loaded:
        out.append("Upload the Infinium sales file.")
    return out


def result_status(metrics: Mapping[str, Any], failed_controls: int, workbook_prepared: bool) -> dict:
    """The status for a finished run: ``blockers``, ``ready``, ``tone`` and ``next_step``.

    A failed control withholds the downloads; a review hold does not block the workpaper but does block
    posting, so it is reported as a blocker on the next step (posting) rather than hidden.
    """
    if str(metrics.get("Control Status")) != "PASS":
        return {"blockers": [
            f"{max(failed_controls, 1)} automated control(s) failed. Downloads are withheld until every control "
            "passes; the Overview tab shows which."], "tone": "fail", "ready": "", "next_step": ""}
    holds = int(metrics.get("Final Disposition - Review Hold Rows", 0) or 0)
    if metrics.get("Posting Status", "READY TO POST") == "REVIEW REQUIRED":
        what = (f"{holds:,} item(s) are on review hold and need a documented decision from a person"
                if holds else "One or more posting blockers remain")
        return {"blockers": [f"{what} before this can be posted. The workpaper can still be prepared."],
                "tone": "blocked", "ready": "", "next_step": ""}
    unresolved = int(metrics.get("Unresolved QuickBooks Rows", 0) or 0)
    if workbook_prepared:
        return {"blockers": [], "tone": "blocked", "ready": "The workpaper is prepared.",
                "next_step": "Download it from the Downloads tab."}
    if unresolved:
        return {"blockers": [], "tone": "blocked",
                "ready": f"Controls passed. {unresolved:,} unresolved item(s) feed the proposed journal entry.",
                "next_step": "Review them in the Exception Review tab, then prepare the workpaper in Downloads."}
    return {"blockers": [], "tone": "blocked", "ready": "Controls passed and every row matched.",
            "next_step": "Prepare the workpaper in the Downloads tab."}


def cleared_note(reasons: Optional[list[str]]) -> str:
    """Why the earlier results are gone, named, so clearing them is never silent."""
    if not reasons:
        return ""
    return f"The previous results were cleared: {', '.join(reasons)}."
