"""Identity-level export controls over one finished reconciliation result.

``validate_reconciliation`` raises on the first inconsistency it finds. These
controls run separately, from the raw result structures rather than from the
ledger the workbook shows, and report every check with its evidence -- so a
workbook is exported only when every sheet's population is provably the same
population. They check *identities* (which rows), then counts, then cents:
two offsetting mistakes can leave totals equal, but not row identities.

These are controls validated at export. They are not live: they run once, when
the result is built or a workbook is generated, and say nothing about edits
made to the workbook afterward. The few figures that stay live in Excel are
named in ``LIVE_WORKBOOK_CONTROLS``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd

from ..duplicates import AMOUNT_CENTS
from .core import (
    _historical_row_indexes,
    cents_or_zero,
    INF_ID,
    QB_ID,
    ReconciliationResult,
    valid_cents,
)
from .labels import (
    DISPOSITION_DUPLICATE_EXCLUDED,
    DISPOSITION_MATCHED,
    DISPOSITION_REVIEW_HOLD,
    DISPOSITION_TRUE_UNMATCHED,
)


SCOPE_AT_EXPORT = "Validated at export (static)"
SCOPE_LIVE = "Live in workbook (formula)"

# What stays live after someone edits the workbook. Everything else on the
# controls table is a snapshot of the moment the file was generated.
LIVE_WORKBOOK_CONTROLS = (
    "Reviewer-adjusted JE on the Posting Summary recalculates from the Reviewer Disposition columns.",
    "Posting Summary 'Live check' compares that live total with the total recorded at export.",
    "Posting Summary counts reviewer decisions that are missing a reviewer, date, or comment.",
)


def _fmt(items: Any, limit: int = 5) -> str:
    items = sorted(str(i) for i in items)
    return ", ".join(items[:limit]) + (f" (+{len(items) - limit} more)" if len(items) > limit else "")


def _check(rows: list[dict[str, Any]], name: str, kind: str, passed: bool, detail: str) -> None:
    rows.append({
        "Check": name, "Basis": kind, "Status": "PASS" if passed else "FAIL",
        "Detail": detail if not passed else (detail or "Agrees."), "Scope": SCOPE_AT_EXPORT,
    })


def verify_result(result: ReconciliationResult) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    qb, inf = result.qb_work, result.inf_work
    qb_id = qb[QB_ID].to_dict()
    inf_id = inf[INF_ID].to_dict()
    ledger = result.qb_dispositions

    # ---- 1. exactly one final disposition per primary source row (identity)
    ledger_ids = list(ledger["QBO Row ID"])
    duplicates = [i for i, n in Counter(ledger_ids).items() if n > 1]
    missing = set(qb[QB_ID]) - set(ledger_ids)
    extra = set(ledger_ids) - set(qb[QB_ID])
    _check(rows, "Every primary QuickBooks row has exactly one final disposition", "Identity",
           not duplicates and not missing and not extra,
           f"duplicated: {_fmt(duplicates)}; missing: {_fmt(missing)}; unknown: {_fmt(extra)}")

    # ---- 2. population identities: the lists every sheet is built from
    matched_q = {qb_id[i] for g in result.matches for i in g.qb_rows} | {
        qb_id[i] for i in _historical_row_indexes(result.historical_clearances, "QuickBooks Primary", "Primary Row Index")
    }
    holds_q = {
        qb_id[i] for i in (
            set(result.duplicate_review_hold_qb_rows) | set(result.amount_variance_review_hold_qb_rows)
            | set(result.ambiguous_duplicate_qb_rows) | set(result.fuzzy_match_review_hold_qb_rows)
            | set(result.reference_hold_qb_rows)
        )
    }
    dup_q = {qb_id[i] for i in result.duplicate_qb_rows}
    unmatched_q = {qb_id[i] for i in result.unmatched_qb}
    populations = {
        DISPOSITION_MATCHED: matched_q, DISPOSITION_REVIEW_HOLD: holds_q,
        DISPOSITION_DUPLICATE_EXCLUDED: dup_q, DISPOSITION_TRUE_UNMATCHED: unmatched_q,
    }
    overlap = [
        f"{a}/{b}" for a in populations for b in populations
        if a < b and populations[a] & populations[b]
    ]
    in_ledger = {d: set(ledger.loc[ledger["Final Disposition"] == d, "QBO Row ID"]) for d in populations}
    mismatched = [d for d in populations if populations[d] != in_ledger[d]]
    _check(rows, "Result populations and the disposition ledger contain the same rows", "Identity",
           not overlap and not mismatched,
           f"overlapping populations: {_fmt(overlap)}; ledger differs for: {_fmt(mismatched)}")
    covered = set().union(*populations.values())
    _check(rows, "Matched + duplicate + hold + unmatched rows cover every source row", "Identity",
           covered == set(qb[QB_ID]), f"uncovered: {_fmt(set(qb[QB_ID]) - covered)}")

    # ---- 3. no record consumed more than once (individual, grouped, historical)
    consumers: dict[str, list[str]] = {}
    for g in result.matches:
        label = g.match_ref or g.match_id or g.method
        for i in g.inf_rows:
            consumers.setdefault(f"INF {inf_id[i]}", []).append(label)
        for i in g.qb_rows:
            consumers.setdefault(f"QB {qb_id[i]}", []).append(label)
    hist = result.historical_clearances
    if not hist.empty:
        for record in hist.to_dict("records"):
            label = str(record["Clearance ID"])
            if record["Primary Row ID"]:
                consumers.setdefault(f"{record['Primary Row ID']}", []).append(label)
            if record["Secondary Row ID"]:
                consumers.setdefault(f"HIST {record['Secondary Row ID']}", []).append(label)
    # a primary row in a historical clearance must not also sit in a primary match
    consumers = {k.replace("INF ", "").replace("QB ", ""): v for k, v in consumers.items()}
    reused = {k: v for k, v in consumers.items() if len(set(v)) > 1 or len(v) > 1}
    # one clearance legitimately lists each of its rows once per group sequence
    reused = {k: v for k, v in reused.items() if len(set(v)) > 1}
    _check(rows, "No source record is consumed by more than one match or clearance", "Identity",
           not reused, "; ".join(f"{k}: {', '.join(sorted(set(v)))}" for k, v in sorted(reused.items())[:5]))
    qb_group_rows = Counter(i for g in result.matches for i in g.qb_rows)
    inf_group_rows = Counter(i for g in result.matches for i in g.inf_rows)
    repeated = [qb_id[i] for i, n in qb_group_rows.items() if n > 1] + [inf_id[i] for i, n in inf_group_rows.items() if n > 1]
    _check(rows, "No row appears twice inside the accepted matches", "Identity", not repeated, _fmt(repeated))

    # ---- 4. accepted matches reconcile to exact signed cents
    bad_cents = []
    for g in result.matches:
        q_total = sum(cents_or_zero(qb.at[i, AMOUNT_CENTS]) for i in g.qb_rows)
        i_total = sum(cents_or_zero(inf.at[i, AMOUNT_CENTS]) for i in g.inf_rows)
        if q_total != i_total or any(not valid_cents(qb.at[i, AMOUNT_CENTS]) for i in g.qb_rows) \
                or any(not valid_cents(inf.at[i, AMOUNT_CENTS]) for i in g.inf_rows):
            bad_cents.append(f"{g.match_ref or g.match_id} ({q_total} vs {i_total} cents)")
    _check(rows, "Every accepted individual and grouped match agrees to the signed cent", "Amount",
           not bad_cents, _fmt(bad_cents))
    if not hist.empty:
        off = []
        for clearance_id, part in hist.groupby("Clearance ID"):
            p = int(round(part["Primary Amount"].sum() * 100))
            s = int(round(part["Secondary Amount"].sum() * 100))
            if p != s:
                off.append(f"{clearance_id} ({p} vs {s} cents)")
        _check(rows, "Every historical clearance agrees to the signed cent", "Amount", not off, _fmt(off))

    # ---- 5. exception / hold identities against their underlying result sets
    sections = Counter()
    unmatched_in_detail: set[str] = set()
    for row in result.paired_rows:
        if row.get("QB Index") is not None and row.get("QB Record Scope") == "Primary":
            sections[row.get("Section")] += 1
            if row.get("Section") == "02 Unmatched QuickBooks":
                unmatched_in_detail.add(qb_id[int(row["QB Index"])])
    _check(rows, "Reconciliation Detail's unmatched rows are exactly the JE-support exceptions", "Identity",
           unmatched_in_detail == unmatched_q,
           f"detail only: {_fmt(unmatched_in_detail - unmatched_q)}; exceptions only: {_fmt(unmatched_q - unmatched_in_detail)}")
    detail_primary = sum(sections.values())
    _check(rows, "Reconciliation Detail lists every primary QuickBooks row once", "Count",
           detail_primary == len(qb), f"{detail_primary} detail rows for {len(qb)} source rows")
    hold_report_ids = set(result.reference_hold_analysis["QuickBooks Row ID"]) if not result.reference_hold_analysis.empty else set()
    _check(rows, "Reference-evidence hold report matches its held rows", "Identity",
           hold_report_ids == {qb_id[i] for i in result.reference_hold_qb_rows},
           f"report only: {_fmt(hold_report_ids - {qb_id[i] for i in result.reference_hold_qb_rows})}")
    variance_ids = set(result.amount_variance_analysis["QuickBooks Row ID"]) if not result.amount_variance_analysis.empty else set()
    _check(rows, "Amount-variance report matches its held rows", "Identity",
           variance_ids == {qb_id[i] for i in result.amount_variance_review_hold_qb_rows}, "")
    ambiguous_ids = set(result.ambiguous_duplicate_analysis["QuickBooks Row ID"]) if not result.ambiguous_duplicate_analysis.empty else set()
    _check(rows, "Ambiguous-candidate report matches its held rows", "Identity",
           ambiguous_ids == {qb_id[i] for i in result.ambiguous_duplicate_qb_rows}, "")

    # ---- 6. counts and dollars agree across detail, ledger, holds, and summary
    metrics = result.metrics
    cents = {i: cents_or_zero(qb.at[i, AMOUNT_CENTS]) for i in qb.index}
    id_to_cents = {qb_id[i]: c for i, c in cents.items()}
    labels = {DISPOSITION_MATCHED: "Matched", DISPOSITION_DUPLICATE_EXCLUDED: "Duplicate Excluded",
              DISPOSITION_REVIEW_HOLD: "Review Hold", DISPOSITION_TRUE_UNMATCHED: "True Unmatched"}
    count_off, amount_off = [], []
    for disposition, label in labels.items():
        ids = populations[disposition]
        if metrics[f"Final Disposition - {label} Rows"] != len(ids):
            count_off.append(label)
        if round(metrics[f"Final Disposition - {label} Amount"] * 100) != sum(id_to_cents[i] for i in ids):
            amount_off.append(label)
    _check(rows, "Summary row counts agree with the row identities in every population", "Count",
           not count_off, _fmt(count_off))
    _check(rows, "Summary dollar totals agree with the row identities in every population", "Amount",
           not amount_off, _fmt(amount_off))
    total = sum(id_to_cents.values())
    _check(rows, "Matched + duplicate + hold + unmatched dollars equal the QuickBooks source total", "Amount",
           sum(sum(id_to_cents[i] for i in ids) for ids in populations.values()) == total
           and round(metrics["QuickBooks Source Total"] * 100) == total,
           f"source total {total} cents")

    # ---- 7. the proposed JE is exactly the eligible exception support
    je_ids = set(ledger.loc[ledger["In Proposed JE"] == "Yes", "QBO Row ID"])
    je_cents = sum(id_to_cents[i] for i in je_ids)
    _check(rows, "Proposed JE rows are exactly the TRUE_UNMATCHED exception rows", "Identity",
           je_ids == unmatched_q, f"JE only: {_fmt(je_ids - unmatched_q)}; exceptions only: {_fmt(unmatched_q - je_ids)}")
    _check(rows, "Proposed JE amount equals the sum of its exception rows", "Amount",
           round(metrics["Proposed JE Amount"] * 100) == je_cents,
           f"{metrics['Proposed JE Amount']} vs {je_cents / 100:.2f}")
    held_in_je = je_ids & holds_q
    _check(rows, "No review-hold or duplicate row feeds the proposed JE", "Identity",
           not held_in_je and not (je_ids & dup_q), _fmt(held_in_je))

    # ---- 8. reviewer adjustments reconcile through the bridge
    adjustments, bridge = result.review_adjustments, result.adjustment_bridge
    if adjustments is not None and not adjustments.empty:
        engine = round(metrics["Proposed JE Amount"] * 100)
        effects = int(sum(round(v * 100) for v in adjustments["JE Effect"]))
        result_line = bridge.loc[bridge["Kind"] == "result", "Amount"].iloc[0]
        _check(rows, "Reviewer-adjusted JE = engine JE + the JE effects of the reviewer adjustments", "Amount",
               round(result_line * 100) == engine + effects, f"{result_line} vs {(engine + effects) / 100:.2f}")
        ids = list(adjustments["QuickBooks Row ID"])
        _check(rows, "Each reviewer adjustment names a distinct primary QuickBooks row", "Identity",
               len(ids) == len(set(ids)) and set(ids) <= set(qb[QB_ID]), "")
        engine_by_row = dict(zip(ledger["QBO Row ID"], ledger["Final Disposition"]))
        changed = [
            r["QuickBooks Row ID"] for r in adjustments.to_dict("records")
            if engine_by_row.get(r["QuickBooks Row ID"]) != r["Engine Final Disposition"]
        ]
        _check(rows, "Reviewer adjustments cite the engine's unchanged disposition", "Identity", not changed, _fmt(changed))
    else:
        _check(rows, "No reviewer adjustments recorded: the adjusted JE equals the engine JE", "Amount", True,
               "No reviewer decisions have been applied to this result.")

    return pd.DataFrame(rows, columns=["Check", "Basis", "Status", "Detail", "Scope"])


def assert_exportable(result: ReconciliationResult) -> pd.DataFrame:
    """Run the controls and refuse to continue (ValueError) if any fails."""
    controls = verify_result(result)
    failed = controls.loc[controls["Status"] != "PASS"]
    if not failed.empty:
        raise ValueError(
            "Export control failure: "
            + "; ".join(f"{r['Check']} ({r['Detail']})" for r in failed.to_dict("records"))
        )
    return controls
