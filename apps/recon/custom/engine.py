"""Generic two-dataset reconciliation driven by a `ReconSpec`.

Rules, in the order they run (the same discipline as the QuickBooks/Infinium engine):

1. Two rows can only match when every mapped alignment column agrees (after the stated cleanup)
   and neither has a blank there. Rows are searched only inside their alignment group.
2. Amounts agree to the signed cent, using integer cents. There is no tolerance and no "closest match".
3. One-to-one runs first. Then groups run in order of size, k = 2, 3, ... up to the limits the user set:
   k rows of one dataset that add up to exactly one row of the other.
4. Ambiguity is never resolved by guessing. If a row could pair with more than one row, or a row sits in
   more than one candidate combination, every row involved is flagged Ambiguous and left for review.
5. Zero-amount rows only match one-to-one: a zero can join any combination, which would make every
   group ambiguous.
6. Nothing is dropped: every input row ends as Matched, Ambiguous, Unmatched or Excluded
   (blank or non-numeric amount, or a blank alignment value), and the controls prove it.
"""

from __future__ import annotations

import re
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from ..matching.core import parse_amount_cents
from .spec import ReconSpec

SEARCH_BUDGET = 4_000_000  # combination steps per alignment group and group size

MATCHED, AMBIGUOUS, UNMATCHED, EXCLUDED = "Matched", "Ambiguous", "Unmatched", "Excluded"
_OPEN = "open"
_CUT_SHORT = "The combination search was cut short for this group (too many rows), so its rows are unresolved."


class _SearchLimit(Exception):
    pass


@dataclass
class ReconResult:
    spec: ReconSpec
    dataset_a: pd.DataFrame  # the uploaded rows plus the Match ID column (if asked) and a status column
    dataset_b: pd.DataFrame
    matches: pd.DataFrame  # one row per match
    detail: pd.DataFrame  # one row per matched source row
    exceptions: pd.DataFrame  # every row that is not matched, with the reason
    bridge: pd.DataFrame  # per-dataset totals: matched, ambiguous, unmatched, excluded
    controls: pd.DataFrame
    warnings: list[str] = field(default_factory=list)

    @property
    def overall(self) -> str:
        statuses = set(self.controls["Status"])
        return "FAIL" if "FAIL" in statuses else "REVIEW" if "REVIEW" in statuses else "PASS"


def normalize_value(value: Any, mode: str) -> Optional[str]:
    """The comparable form of one alignment cell, or None when it is blank or unreadable."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if mode == "number":
        cents = parse_amount_cents(value)
        return None if cents is None else str(cents)
    if mode == "date":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            stamp = pd.to_datetime(str(value).strip() if isinstance(value, str) else value,
                                   errors="coerce", format="mixed")
        return None if pd.isna(stamp) else stamp.date().isoformat()
    text = str(value).strip()
    if mode == "alnum":
        text = re.sub(r"[^0-9A-Za-z]", "", text).casefold()
    elif mode == "digits":
        text = re.sub(r"\D", "", text)
    else:
        text = " ".join(text.split()).casefold()
    return text or None


def _find_sums(items: list[tuple[int, int]], target: int, k: int, limit: int, budget: list[int]) -> list[list[int]]:
    """Up to `limit` combinations of exactly k items whose cents add up to `target`."""
    n = len(items)
    positions: dict[int, list[int]] = defaultdict(list)
    for position, (_, cents) in enumerate(items):
        positions[cents].append(position)
    found: list[list[int]] = []

    def search(start: int, left: int, need: int, chosen: list[int]) -> None:
        if left == 1:
            for position in positions.get(need, ()):
                if position >= start:
                    found.append(chosen + [items[position][0]])
                    if len(found) >= limit:
                        return
            return
        for position in range(start, n - left + 1):
            budget[0] -= 1
            if budget[0] < 0:
                raise _SearchLimit
            search(position + 1, left - 1, need - items[position][1], chosen + [items[position][0]])
            if len(found) >= limit:
                return

    search(0, k, target, [])
    return found


class _Side:
    """One dataset prepared for matching: cents, alignment keys, ids and a row status."""

    def __init__(self, frame: pd.DataFrame, label: str, amount_col: str, align_cols: list[str],
                 modes: list[str], id_col: Optional[str]):
        self.label = label
        self.frame = frame.reset_index(drop=True)
        count = len(self.frame)
        self.cents: list[Optional[int]] = [parse_amount_cents(v) for v in self.frame[amount_col]]
        columns = [self.frame[c].tolist() for c in align_cols]
        self.keys: list[Optional[tuple]] = []
        for i in range(count):
            parts = tuple(normalize_value(col[i], mode) for col, mode in zip(columns, modes))
            self.keys.append(None if any(p is None for p in parts) else parts)
        if id_col:
            self.row_ids = [f"Row {i + 1}" if pd.isna(v) or not str(v).strip() else str(v).strip()
                            for i, v in enumerate(self.frame[id_col])]
        else:
            self.row_ids = [f"Row {i + 1}" for i in range(count)]
        self.status = [_OPEN] * count
        self.reason = [""] * count
        self.match_of: list[Optional[int]] = [None] * count
        for i in range(count):
            if self.cents[i] is None:
                self.status[i], self.reason[i] = EXCLUDED, "The amount is blank or is not a number."
            elif self.keys[i] is None:
                self.status[i], self.reason[i] = EXCLUDED, "A column used to align rows is blank in this row."

    def open_rows(self, rows: list[int]) -> list[int]:
        return [i for i in rows if self.status[i] == _OPEN]

    def mark_ambiguous(self, rows, reason: str) -> None:
        for i in rows:
            if self.status[i] == _OPEN:
                self.status[i], self.reason[i] = AMBIGUOUS, reason


def reconcile(frame_a: pd.DataFrame, frame_b: pd.DataFrame, spec: ReconSpec) -> ReconResult:
    """Run `spec` over two datasets and return every output the page and workbook need."""
    if spec.amount is None:
        raise ValueError("The mapping has no amount column.")
    modes = [p.normalize for p in spec.align]
    a = _Side(frame_a, spec.label_a, spec.amount.a, [p.a for p in spec.align], modes, spec.id_a)
    b = _Side(frame_b, spec.label_b, spec.amount.b, [p.b for p in spec.align], modes, spec.id_b)

    buckets: dict[tuple, tuple[list[int], list[int]]] = {}
    for side_no, side in enumerate((a, b)):
        for i, key in enumerate(side.keys):
            if side.status[i] == _OPEN:
                buckets.setdefault(key, ([], []))[side_no].append(i)

    groups: list[tuple[str, list[int], list[int]]] = []  # (type, rows in A, rows in B)
    limit_hit: set[tuple] = set()

    def take(kind: str, rows_a: list[int], rows_b: list[int]) -> None:
        number = len(groups)
        for side, rows in ((a, rows_a), (b, rows_b)):
            for i in rows:
                side.status[i], side.match_of[i] = MATCHED, number
        groups.append((kind, rows_a, rows_b))

    # Pass 1: one to one.
    for rows_a, rows_b in buckets.values():
        by_a: dict[int, list[int]] = defaultdict(list)
        by_b: dict[int, list[int]] = defaultdict(list)
        for i in a.open_rows(rows_a):
            by_a[a.cents[i]].append(i)
        for j in b.open_rows(rows_b):
            by_b[b.cents[j]].append(j)
        for cents, left in by_a.items():
            right = by_b.get(cents)
            if not right:
                continue
            if len(left) == 1 and len(right) == 1:
                take("1 to 1", left, right)
            else:
                why = (f"{len(left)} rows in {a.label} and {len(right)} rows in {b.label} have the same alignment "
                       "values and the same amount, so the pairing cannot be determined. "
                       "Add an alignment column that tells them apart.")
                a.mark_ambiguous(left, why)
                b.mark_ambiguous(right, why)

    # Passes 2 and up: k rows of one dataset add up to one row of the other, smallest k first.
    plan = sorted([(k, 0) for k in range(2, spec.max_a_per_b + 1)] + [(k, 1) for k in range(2, spec.max_b_per_a + 1)])
    for k, direction in plan:
        many, one = (a, b) if direction == 0 else (b, a)
        for key, (rows_a, rows_b) in buckets.items():
            many_rows, one_rows = (rows_a, rows_b) if direction == 0 else (rows_b, rows_a)
            pool = [(i, many.cents[i]) for i in many.open_rows(many_rows) if many.cents[i] != 0]
            targets = one.open_rows(one_rows)
            if len(pool) < k or not targets:
                continue
            budget = [SEARCH_BUDGET]
            try:
                solutions = {j: _find_sums(pool, one.cents[j], k, 2, budget) for j in targets}
            except _SearchLimit:
                limit_hit.add(key)
                continue
            claimed: dict[int, set[int]] = defaultdict(set)
            for j, found in solutions.items():
                for combo in found:
                    for i in combo:
                        claimed[i].add(j)
            for j in targets:
                found = solutions[j]
                if not found:
                    continue
                if len(found) == 1 and all(claimed[i] == {j} for i in found[0]):
                    rows = sorted(found[0])
                    if direction == 0:
                        take(f"{k} {a.label} to 1 {b.label}", rows, [j])
                    else:
                        take(f"{k} {b.label} to 1 {a.label}", [j], rows)
                else:
                    why = (f"More than one combination of {k} {many.label} rows can add up to this amount, or the "
                           "same row could belong to more than one match, so no match was chosen.")
                    many.mark_ambiguous({i for combo in found for i in combo}, why)
                    one.mark_ambiguous([j], why)

    # Whatever is still open is unmatched, with the reason it could not match.
    for key, (rows_a, rows_b) in buckets.items():
        for side, other, other_rows in ((a, b, rows_b), (b, a, rows_a)):
            for i in side.open_rows(rows_a if side is a else rows_b):
                side.status[i] = UNMATCHED
                if key in limit_hit:
                    side.reason[i] = _CUT_SHORT
                elif not other_rows:
                    side.reason[i] = f"No {other.label} row has the same alignment values."
                else:
                    side.reason[i] = (f"No {other.label} row, or combination of rows within the limits set, "
                                      "equals this amount.")
    notes = []
    if limit_hit:
        notes.append(f"The combination search stopped early in {len(limit_hit)} alignment group(s); their rows are "
                     "left unmatched. Add alignment columns to make the groups smaller.")
    return _assemble(spec, a, b, groups, notes)


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

def _dollars(cents: Optional[int]) -> Optional[float]:
    return None if cents is None else cents / 100


def _annotated(side: _Side, spec: ReconSpec, match_ids: dict[int, str], after: Optional[str]) -> pd.DataFrame:
    """The uploaded rows with the Match ID column placed where the user asked, and a status column last."""
    out = side.frame.copy()
    ids = ["" if side.match_of[i] is None else match_ids[side.match_of[i]] for i in range(len(out))]
    status_name = "Recon Status"
    while status_name in out.columns:
        status_name += " (2)"
    out[status_name] = side.status
    if spec.add_match_id:
        header = spec.match_id_header or "Match ID"
        while header in out.columns:
            header += " (2)"
        if spec.match_id_position == "first":
            loc = 0
        elif spec.match_id_position == "after" and after in out.columns:
            loc = out.columns.get_loc(after) + 1
        else:
            loc = len(out.columns) - 1  # after the last original column, before the status column
        out.insert(loc, header, ids)
    return out


def _assemble(spec: ReconSpec, a: _Side, b: _Side, groups, notes: list[str]) -> ReconResult:
    order = sorted(range(len(groups)), key=lambda n: (min(groups[n][1] or [10**9]), min(groups[n][2] or [10**9])))
    match_ids = {n: f"M-{rank + 1:04d}" for rank, n in enumerate(order)}

    match_rows, detail_rows = [], []
    for n in order:
        kind, rows_a, rows_b = groups[n]
        total_a = sum(a.cents[i] for i in rows_a)
        total_b = sum(b.cents[j] for j in rows_b)
        match_rows.append({
            "Match ID": match_ids[n], "Match Type": kind,
            f"{a.label} Rows": len(rows_a), f"{b.label} Rows": len(rows_b),
            f"{a.label} IDs": "; ".join(a.row_ids[i] for i in rows_a),
            f"{b.label} IDs": "; ".join(b.row_ids[j] for j in rows_b),
            f"{a.label} Total": _dollars(total_a), f"{b.label} Total": _dollars(total_b),
            "Difference": _dollars(total_a - total_b),
        })
        for side, rows in ((a, rows_a), (b, rows_b)):
            for i in rows:
                detail_rows.append({"Match ID": match_ids[n], "Match Type": kind, "Dataset": side.label,
                                    "Row ID": side.row_ids[i], "Source Row": i + 1, "Amount": _dollars(side.cents[i])})
    matches = pd.DataFrame(match_rows, columns=[
        "Match ID", "Match Type", f"{a.label} Rows", f"{b.label} Rows", f"{a.label} IDs", f"{b.label} IDs",
        f"{a.label} Total", f"{b.label} Total", "Difference"])
    detail = pd.DataFrame(detail_rows, columns=["Match ID", "Match Type", "Dataset", "Row ID", "Source Row", "Amount"])

    exception_rows = [
        {"Dataset": side.label, "Row ID": side.row_ids[i], "Source Row": i + 1,
         "Amount": _dollars(side.cents[i]), "Status": state, "Reason": side.reason[i]}
        for side in (a, b) for i, state in enumerate(side.status) if state != MATCHED
    ]
    exceptions = pd.DataFrame(exception_rows, columns=["Dataset", "Row ID", "Source Row", "Amount", "Status", "Reason"])

    bridge_rows = []
    for side in (a, b):
        row: dict[str, Any] = {"Dataset": side.label, "Rows": len(side.status),
                               "Total Amount": _dollars(sum(c or 0 for c in side.cents))}
        for state in (MATCHED, AMBIGUOUS, UNMATCHED, EXCLUDED):
            picked = [i for i, s in enumerate(side.status) if s == state]
            row[f"{state} Rows"] = len(picked)
            row[f"{state} Amount"] = _dollars(sum(side.cents[i] or 0 for i in picked))
        bridge_rows.append(row)

    return ReconResult(
        spec=spec,
        dataset_a=_annotated(a, spec, match_ids, spec.match_id_after_a),
        dataset_b=_annotated(b, spec, match_ids, spec.match_id_after_b),
        matches=matches, detail=detail, exceptions=exceptions, bridge=pd.DataFrame(bridge_rows),
        controls=_controls(spec, a, b, groups), warnings=notes,
    )


def _controls(spec: ReconSpec, a: _Side, b: _Side, groups) -> pd.DataFrame:
    rows: list[tuple[str, str, str]] = []
    for side in (a, b):
        counts = Counter(side.status)
        accounted = sum(counts[s] for s in (MATCHED, AMBIGUOUS, UNMATCHED, EXCLUDED))
        rows.append((f"Every {side.label} row is accounted for", "PASS" if accounted == len(side.status) else "FAIL",
                     f"{len(side.status)} rows read; {counts[MATCHED]} matched, {counts[AMBIGUOUS]} ambiguous, "
                     f"{counts[UNMATCHED]} unmatched, {counts[EXCLUDED]} excluded."))

    total_a = sum(a.cents[i] for _, ra, _rb in groups for i in ra)
    total_b = sum(b.cents[j] for _, _ra, rb in groups for j in rb)
    rows.append(("Matched amounts agree between the datasets", "PASS" if total_a == total_b else "FAIL",
                 f"{a.label} {total_a / 100:,.2f} vs {b.label} {total_b / 100:,.2f}."))
    off = sum(1 for _, ra, rb in groups if sum(a.cents[i] for i in ra) != sum(b.cents[j] for j in rb))
    rows.append(("Every match agrees to the cent", "PASS" if off == 0 else "FAIL",
                 f"{len(groups)} matches; {off} differ."))
    used = [(0, i) for _, ra, _rb in groups for i in ra] + [(1, j) for _, _ra, rb in groups for j in rb]
    rows.append(("No row is in more than one match", "PASS" if len(used) == len(set(used)) else "FAIL",
                 f"{len(used)} matched rows."))

    for side, column in ((a, spec.id_a), (b, spec.id_b)):
        if column:
            repeated = [k for k, v in Counter(side.row_ids).items() if v > 1]
            rows.append((f"{side.label} ID column '{column}' is unique", "REVIEW" if repeated else "PASS",
                         f"{len(repeated)} repeated ID(s), e.g. {', '.join(repeated[:3])}." if repeated
                         else "No repeated IDs."))
    for status, label in ((AMBIGUOUS, "No ambiguous rows left for review"), (EXCLUDED, "No excluded rows")):
        n = sum(s == status for side in (a, b) for s in side.status)
        rows.append((label, "REVIEW" if n else "PASS", f"{n} row(s)."))
    if any(r == _CUT_SHORT for side in (a, b) for r in side.reason):
        rows.append(("Combination search completed", "REVIEW", "Stopped early in at least one group."))
    return pd.DataFrame(rows, columns=["Check", "Status", "Detail"])
