"""The mapping a user builds for the Custom Reconciliation page, and its save/load format.

A `ReconSpec` says which columns to reconcile on, which supporting columns must align before two
rows may match, where a unique ID lives, how many rows may add up to one, and where the Match ID
column goes. It holds column *names* only, never data, so it is safe to keep in the session or to
download as JSON and load again later.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional

SPEC_VERSION = 1
MAX_GROUP_SIZE = 6  # rows summed into one: the search grows quickly beyond this

# How an alignment column's values are compared. Every mode is an exact comparison after the
# stated cleanup; there is no "close enough" mode.
NORMALIZERS = {
    "text": "Text (ignore case and extra spaces)",
    "alnum": "Letters and digits only (ignore punctuation and spaces)",
    "digits": "Digits only (for example 'PO 800000' matches '800000')",
    "number": "Amount (equal to the cent)",
    "date": "Date (same calendar day)",
}
ID_POSITIONS = {"first": "First column", "last": "Last column", "after": "After a chosen column"}


@dataclass
class ColumnPair:
    a: str
    b: str
    normalize: str = "text"


@dataclass
class ReconSpec:
    name: str
    label_a: str = "Dataset A"
    label_b: str = "Dataset B"
    amount: Optional[ColumnPair] = None
    align: list[ColumnPair] = field(default_factory=list)
    id_a: Optional[str] = None  # an existing unique ID column in each dataset, if there is one
    id_b: Optional[str] = None
    max_a_per_b: int = 1  # up to this many A rows may add up to one B row
    max_b_per_a: int = 1
    add_match_id: bool = True
    match_id_header: str = "Match ID"
    match_id_position: str = "last"
    match_id_after_a: Optional[str] = None
    match_id_after_b: Optional[str] = None
    version: int = SPEC_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReconSpec":
        """Rebuild a spec from saved data, ignoring unknown keys and clamping values to valid ones."""
        def pair(raw: Any) -> Optional[ColumnPair]:
            if not isinstance(raw, dict) or not raw.get("a") or not raw.get("b"):
                return None
            mode = raw.get("normalize", "text")
            return ColumnPair(str(raw["a"]), str(raw["b"]), mode if mode in NORMALIZERS else "text")

        def size(raw: Any) -> int:
            try:
                return max(1, min(MAX_GROUP_SIZE, int(raw)))
            except (TypeError, ValueError):
                return 1

        position = data.get("match_id_position", "last")
        return cls(
            name=str(data.get("name") or "Untitled mapping"),
            label_a=str(data.get("label_a") or "Dataset A"),
            label_b=str(data.get("label_b") or "Dataset B"),
            amount=pair(data.get("amount")),
            align=[p for p in (pair(item) for item in data.get("align") or []) if p],
            id_a=data.get("id_a") or None,
            id_b=data.get("id_b") or None,
            max_a_per_b=size(data.get("max_a_per_b", 1)),
            max_b_per_a=size(data.get("max_b_per_a", 1)),
            add_match_id=bool(data.get("add_match_id", True)),
            match_id_header=str(data.get("match_id_header") or "Match ID"),
            match_id_position=position if position in ID_POSITIONS else "last",
            match_id_after_a=data.get("match_id_after_a") or None,
            match_id_after_b=data.get("match_id_after_b") or None,
        )

    @classmethod
    def from_json(cls, text: str) -> "ReconSpec":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("A saved mapping must be a JSON object.")
        return cls.from_dict(data)


def _norm_header(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def find_column(columns: Iterable[str], wanted: Optional[str]) -> Optional[str]:
    """The file's column for a saved name: exact first, then ignoring case and spacing; else None."""
    if not wanted:
        return None
    columns = list(columns)
    if wanted in columns:
        return wanted
    target = _norm_header(wanted)
    matches = [c for c in columns if _norm_header(c) == target]
    return matches[0] if len(matches) == 1 else None


def missing_columns(spec: ReconSpec, columns_a: Iterable[str], columns_b: Iterable[str]) -> list[str]:
    """Saved columns the uploaded files do not have, as readable messages (empty = the preset fits)."""
    columns_a, columns_b = list(columns_a), list(columns_b)
    problems: list[str] = []
    wanted: list[tuple[str, Optional[str], list[str]]] = []
    if spec.amount:
        wanted += [(spec.label_a, spec.amount.a, columns_a), (spec.label_b, spec.amount.b, columns_b)]
    for pair in spec.align:
        wanted += [(spec.label_a, pair.a, columns_a), (spec.label_b, pair.b, columns_b)]
    wanted += [(spec.label_a, spec.id_a, columns_a), (spec.label_b, spec.id_b, columns_b)]
    for label, name, columns in wanted:
        if name and find_column(columns, name) is None:
            problems.append(f"{label} has no column named '{name}'.")
    return problems


def validate_spec(spec: ReconSpec, columns_a: Iterable[str], columns_b: Iterable[str]) -> list[str]:
    """Blocking problems with a finished mapping; an empty list means it can run."""
    columns_a, columns_b = list(columns_a), list(columns_b)
    problems: list[str] = []
    if spec.amount is None or not spec.amount.a or not spec.amount.b:
        problems.append("Choose the amount column in both datasets.")
    elif spec.amount.a not in columns_a or spec.amount.b not in columns_b:
        problems.append("The amount column is not in the uploaded file.")
    seen: set[tuple[str, str]] = set()
    for index, pair in enumerate(spec.align, start=1):
        if pair.a not in columns_a or pair.b not in columns_b:
            problems.append(f"Alignment column {index} is incomplete.")
        elif (pair.a, pair.b) in seen:
            problems.append(f"Alignment column {index} repeats an earlier pair.")
        seen.add((pair.a, pair.b))
    for column, columns, label in ((spec.id_a, columns_a, spec.label_a), (spec.id_b, columns_b, spec.label_b)):
        if column and column not in columns:
            problems.append(f"The ID column for {label} is not in the uploaded file.")
    if spec.label_a.strip().casefold() == spec.label_b.strip().casefold():
        problems.append("Give the two datasets different names.")
    if spec.add_match_id and spec.match_id_position == "after":
        if spec.match_id_after_a not in columns_a or spec.match_id_after_b not in columns_b:
            problems.append("Choose the column the Match ID should follow in each dataset.")
    return problems
