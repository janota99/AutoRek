"""Where the period is in upload > validate > preview > close, and exactly what blocks the next step.

Pure and display-side: it reads facts the page already computed (errors, the preview's control
counts, whether the preview is stale) and words them. It never decides whether a period may close;
``app.py`` keeps its own ``commit_blocked`` for that, and ``blocks_close`` here mirrors that rule
so a test can prove the two agree.
"""
from __future__ import annotations

from typing import Optional

STEPS = ("Upload", "Validate", "Preview", "Close period")
UPLOAD, VALIDATE, PREVIEW, CLOSE = range(4)

_MAX_LISTED = 3


def _listed(messages: list[str]) -> list[str]:
    """The first few messages verbatim, then a count, so a long list stays one glance."""
    shown = list(messages[:_MAX_LISTED])
    if len(messages) > _MAX_LISTED:
        shown.append(f"...and {len(messages) - _MAX_LISTED} more (listed under Batch Processing).")
    return shown


def blocks_close(*, stale: bool, already_closed: bool, sequence_error: Optional[str], input_errors: int,
                 processing_errors: int, fail: int, review_needs_ack: bool, review_ack: bool, demo: bool) -> bool:
    """The page's close gate, restated. Keep in step with ``commit_blocked`` in app.py."""
    return bool(stale or already_closed or sequence_error or input_errors or processing_errors
                or fail > 0 or (review_needs_ack and not review_ack) or demo)


def period_status(*, period_key: str, has_grid_data: bool, input_errors: list[str],
                  sequence_error: Optional[str], already_closed: bool,
                  preview: Optional[dict] = None) -> dict:
    """The strip position, per-step status words, blockers and next step for the period page.

    ``preview`` is None until a preview exists for this period, else a dict with: ``stale`` (bool),
    ``changed`` (labels of inputs changed since the preview), ``fail`` / ``review`` (product counts),
    ``warnings`` (receipt warnings), ``processing_errors`` and ``receipt_errors`` (counts),
    ``review_ack`` (bool) and ``demo`` (bool, sample files loaded).
    """
    details = ["Ending inventory loaded" if has_grid_data else "Waiting for ending inventory",
               "After upload", "Not calculated", "Locked"]

    if already_closed:
        return {"step": CLOSE, "details": ["Done", "Done", "Done", "Closed"], "blockers": [], "tone": "blocked",
                "ready": f"{period_key} is closed.",
                "next_step": "To change it, reopen the latest closed period in History & Snapshots."}

    blockers: list[str] = []
    problems = _listed(list(dict.fromkeys(input_errors)))
    if problems:
        blockers += problems
        details[VALIDATE] = f"{len(set(input_errors))} to fix"
    elif has_grid_data:
        details[VALIDATE] = "No problems"
    if sequence_error:
        blockers.append(sequence_error)
        details[VALIDATE] = "Sequence"

    if preview is None:
        if blockers:
            return {"step": UPLOAD if not has_grid_data else VALIDATE, "details": details, "blockers": blockers,
                    "tone": "blocked", "ready": "", "next_step": ""}
        if not has_grid_data:
            return {"step": UPLOAD, "details": details, "tone": "blocked", "ready": "", "next_step": "",
                    "blockers": ["Add the ending inventory (upload a file, use sample data, or enter it manually)."]}
        return {"step": PREVIEW, "details": details, "blockers": [], "tone": "blocked",
                "ready": "Ready to calculate a preview.",
                "next_step": "A preview changes nothing; official layers change only when you close the period."}

    fail, review = int(preview.get("fail", 0)), int(preview.get("review", 0))
    needs_ack = review > 0 or int(preview.get("warnings", 0)) > 0
    details[PREVIEW] = "Out of date" if preview.get("stale") else "Calculated"
    fatal = False
    if preview.get("stale"):
        changed = preview.get("changed") or []
        what = f" ({', '.join(changed)})" if changed else ""
        blockers.append(f"Inputs changed since the preview was calculated{what}. Recalculate before closing.")
    if preview.get("processing_errors"):
        blockers.append(f"{preview['processing_errors']} product(s) could not be calculated, so they are not in this preview.")
    if preview.get("receipt_errors"):
        blockers.append("Rejected receipt rows are excluded from this preview. Correct them and recalculate.")
    if fail:
        fatal = True
        blockers.append(f"{fail} product(s) FAIL their controls. Closing is blocked until they are fixed.")
    if needs_ack and not preview.get("review_ack"):
        blockers.append("Acknowledge the REVIEW items and input warnings before closing.")
    if preview.get("demo"):
        blockers.append("Demo data is loaded. Close is disabled so demo numbers can never become official layers.")

    if blockers:
        details[CLOSE] = "Blocked"
        return {"step": PREVIEW, "details": details, "blockers": blockers,
                "tone": "fail" if fatal else "blocked", "ready": "", "next_step": ""}
    details[CLOSE] = "Ready"
    return {"step": CLOSE, "details": details, "blockers": [], "tone": "blocked",
            "ready": f"Ready to close {period_key}.",
            "next_step": "Closing saves these ending balances as the next period's beginning balances."}
