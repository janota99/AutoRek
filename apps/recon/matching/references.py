"""Match reference IDs: assigning them, carrying them onto review candidates, and validating them."""

from __future__ import annotations

import re
from typing import Any, Optional

import pandas as pd

from ..duplicates import (
    DUPLICATE_BASIS_CROSS_SCOPE,
    DUPLICATE_BASIS_INVOICE_ONLY,
    DUPLICATE_BASIS_PO_ONLY,
    DUPLICATE_BASIS_STRICT,
    NORM_INV,
    NORM_PO,
    SOURCE_POS,
)
from .core import _amount_total, _split_reference_ids, cents_to_float, INF_ID, QB_ID
from .labels import (
    _ALREADY_MATCHED_DISPOSITION,
    MATCH_REF_COLUMN,
    MATCH_REGISTER_COLUMNS,
    MATCH_TYPE_GROUPED,
    MATCH_TYPE_ONE_TO_ONE,
    REFERENCE_HOLD_SECTION,
    REFERENCED_MATCH_REF_COLUMN,
)


# ---------------------------------------------------------------------------
# Match references
# ---------------------------------------------------------------------------
# One concise, user-facing reference per accepted reconciliation relationship:
# M-001, M-002, ... for a one-to-one match and G-001, G-002, ... for a grouped
# (aggregate) match whose records are accepted together. References are
# assigned exactly once, in build_reconciliation, after matching and
# historical clearance are final and before any workbook sorts or renders
# anything -- every workbook then displays the same canonical value stored on
# the ReconciliationResult, never one generated at write time.

_MATCH_REF_PATTERN = re.compile(r"^([MG])-(\d{3,})$")


def _match_ref_sort_key(reference: str) -> tuple[int, int]:
    matched = _MATCH_REF_PATTERN.match(reference)
    if not matched:
        return (2, 0)
    return ("MG".index(matched.group(1)), int(matched.group(2)))


def _format_match_ref(prefix: str, number: int, width: int) -> str:
    return f"{prefix}-{number:0{width}d}"


def assign_match_references(
    matches: list["MatchGroup"],
    historical_clearances: pd.DataFrame,
    qb: pd.DataFrame,
    inf: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Assign one reference per final accepted relationship.

    Returns (register, historical_clearances_with_refs). The register has one
    row per accepted relationship. Nothing about which records matched, or
    why, is changed -- this only labels the relationships already decided.

    * A relationship with one QuickBooks and one Infinium record is
      "one-to-one" and receives M-###; one with more than one record on
      either side is "grouped" and receives a single G-### shared by every
      participating record on both sides.
    * Numbering follows a canonical order that depends only on the input
      files' own row order -- primary matches by the earliest QuickBooks
      then Infinium source position, then prior-period (historical)
      clearances in clearance-ID order -- never on any worksheet's sort.
    * Zero-padding is three digits and widens automatically for a series
      that passes 999.
    """
    def earliest(frame: pd.DataFrame, indexes: Any) -> int:
        positions = [int(frame.at[idx, SOURCE_POS]) for idx in indexes]
        return min(positions) if positions else -1

    relationships: list[dict[str, Any]] = []
    for group in sorted(
        matches, key=lambda g: (earliest(qb, g.qb_rows), earliest(inf, g.inf_rows)),
    ):
        ordered_q = sorted(group.qb_rows, key=lambda idx: qb.at[idx, SOURCE_POS])
        ordered_i = sorted(group.inf_rows, key=lambda idx: inf.at[idx, SOURCE_POS])
        relationships.append({
            "group": group,
            "clearance_id": None,
            "scope": "Primary",
            "basis": group.method,
            "qb_ids": [str(qb.at[idx, QB_ID]) for idx in ordered_q],
            "inf_ids": [str(inf.at[idx, INF_ID]) for idx in ordered_i],
            "qb_amount": cents_to_float(_amount_total(qb, ordered_q)),
            "inf_amount": cents_to_float(_amount_total(inf, ordered_i)),
            "key": "",
        })

    if not historical_clearances.empty:
        for clearance_id, rows in sorted(
            historical_clearances.groupby("Clearance ID", sort=False), key=lambda item: str(item[0]),
        ):
            ordered = rows.sort_values("Group Sequence", kind="stable")
            first = ordered.iloc[0]
            primary_ids = [str(value) for value in ordered["Primary Row ID"] if value]
            secondary_ids = [str(value) for value in ordered["Secondary Row ID"] if value]
            primary_is_qb = first["Primary Dataset"] == "QuickBooks Primary"
            relationships.append({
                "group": None,
                "clearance_id": clearance_id,
                "scope": "Historical (prior period)",
                "basis": str(first["Match Method"]),
                "qb_ids": primary_ids if primary_is_qb else secondary_ids,
                "inf_ids": secondary_ids if primary_is_qb else primary_ids,
                "qb_amount": round(float(ordered["Primary Amount" if primary_is_qb else "Secondary Amount"].sum()), 2),
                "inf_amount": round(float(ordered["Secondary Amount" if primary_is_qb else "Primary Amount"].sum()), 2),
                "key": clearance_id,
            })

    for relationship in relationships:
        relationship["grouped"] = (
            len(relationship["qb_ids"]) > 1 or len(relationship["inf_ids"]) > 1
        )
    grouped_total = sum(1 for r in relationships if r["grouped"])
    one_to_one_total = len(relationships) - grouped_total
    widths = {
        "M": max(3, len(str(one_to_one_total))),
        "G": max(3, len(str(grouped_total))),
    }

    counters = {"M": 0, "G": 0}
    register_records: list[dict[str, Any]] = []
    reference_by_clearance: dict[Any, str] = {}
    for relationship in relationships:
        prefix = "G" if relationship["grouped"] else "M"
        counters[prefix] += 1
        reference = _format_match_ref(prefix, counters[prefix], widths[prefix])
        group = relationship["group"]
        if group is not None:
            # The accepted relationship's one visible identifier replaces the
            # run-local sequence perform_matching assigned before fuzzy holds
            # were pulled out (which left gaps and a second, longer format).
            group.match_ref = reference
            group.match_id = reference
        else:
            reference_by_clearance[relationship["clearance_id"]] = reference
        register_records.append({
            "Match Ref.": reference,
            "Match Type": MATCH_TYPE_GROUPED if relationship["grouped"] else MATCH_TYPE_ONE_TO_ONE,
            "Record Scope": relationship["scope"],
            "Match Basis": relationship["basis"],
            "QuickBooks Row Count": len(relationship["qb_ids"]),
            "Infinium Row Count": len(relationship["inf_ids"]),
            "QuickBooks Amount": relationship["qb_amount"],
            "Infinium Amount": relationship["inf_amount"],
            "Amount Difference": round(relationship["qb_amount"] - relationship["inf_amount"], 2),
            "QuickBooks Row IDs": "; ".join(relationship["qb_ids"]),
            "Infinium Row IDs": "; ".join(relationship["inf_ids"]),
            "Clearance ID": relationship["key"],
        })

    register = pd.DataFrame(register_records, columns=MATCH_REGISTER_COLUMNS)
    clearances = historical_clearances.copy()
    if MATCH_REF_COLUMN not in clearances.columns:
        location = (
            list(clearances.columns).index("Match Method")
            if "Match Method" in clearances.columns else len(clearances.columns)
        )
        clearances.insert(
            location, MATCH_REF_COLUMN,
            clearances["Clearance ID"].map(reference_by_clearance).fillna("")
            if "Clearance ID" in clearances.columns else "",
        )
    return register, clearances


def _match_reference_by_record_id(register: pd.DataFrame) -> dict[str, str]:
    """Row ID (QuickBooks or Infinium, primary or historical) -> the accepted
    relationship's reference. QuickBooks and Infinium IDs carry distinct
    prefixes, so one flat map is unambiguous."""
    mapping: dict[str, str] = {}
    if register is None or register.empty:
        return mapping
    for record in register.to_dict("records"):
        for column in ("QuickBooks Row IDs", "Infinium Row IDs"):
            for record_id in _split_reference_ids(record[column]):
                mapping.setdefault(record_id, record["Match Ref."])
    return mapping


def _match_phrase(references: list[str], group: bool = False) -> str:
    noun = "Group Match" if group else "Match"
    if len(references) == 1:
        return f"{noun} {references[0]}"
    listed = (
        f"{references[0]} and {references[1]}" if len(references) == 2
        else f"{', '.join(references[:-1])}, and {references[-1]}"
    )
    return f"{noun}{'es' if noun.endswith('h') else 's'} {listed}"


def describe_match_references(references: list[str], group: bool = False) -> str:
    """"Match M-015", "Matches M-015 and M-016", "Group Match G-009" -- the
    one wording every sheet and message uses to name accepted matches."""
    return _match_phrase(references, group=group)


def _reference_message(references: list[str], basis: str) -> str:
    """The specific relationship an exception points at, replacing a broad
    "a match has already been applied" style message."""
    if basis == "Group":
        return f"Review: Candidate belongs to {_match_phrase(references, group=True)}."
    if basis in ("PO", "Invoice"):
        return f"Review: {basis} already used by {_match_phrase(references)}."
    return f"Infinium record already assigned to {_match_phrase(references)}."


_DUPLICATE_BASIS_PHRASES = {
    DUPLICATE_BASIS_STRICT: "PO, Invoice, and Amount",
    DUPLICATE_BASIS_PO_ONLY: "PO and Amount",
    DUPLICATE_BASIS_INVOICE_ONLY: "Invoice and Amount",
    DUPLICATE_BASIS_CROSS_SCOPE: "PO, Invoice, and Amount across periods",
}


def _duplicate_message(references: list[str], basis: str, exact: bool = False) -> str:
    """An exact (excluded) duplicate is stated as a fact; anything weaker stays
    a "potential" duplicate."""
    phrase = _DUPLICATE_BASIS_PHRASES.get(basis)
    suffix = f" (same {phrase})" if phrase else ""
    return f"{'Exact' if exact else 'Potential'} duplicate of {_match_phrase(references)}{suffix}."


def refine_candidates_with_match_references(
    candidates: pd.DataFrame,
    register: pd.DataFrame,
    qb: pd.DataFrame,
    inf: pd.DataFrame,
    duplicate_report: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Add "Referenced Match Ref." / "Reference Basis" to the candidate
    table and, where an unresolved QuickBooks row shares its PO/invoice with
    an Infinium row that an accepted match already consumed -- or is a
    potential duplicate of a QuickBooks row an accepted match already
    consumed -- replace the broad disposition with a message naming that
    exact match. The disposition is only reworded: the row stays an
    unresolved exception with the same cause, confidence, and accrual
    treatment."""
    refined = candidates.copy()
    refined[REFERENCED_MATCH_REF_COLUMN] = ""
    refined["Reference Basis"] = ""
    if refined.empty or register.empty:
        return refined
    reference_by_id = _match_reference_by_record_id(register)
    qb_refs = qb.set_index(QB_ID)[[NORM_PO, NORM_INV]].to_dict("index")
    inf_refs = inf.set_index(INF_ID)[[NORM_PO, NORM_INV]].to_dict("index")
    duplicate_by_qb_id: dict[str, dict[str, Any]] = {}
    if duplicate_report is not None and not duplicate_report.empty:
        for entry in duplicate_report.loc[duplicate_report["Source Scope"].eq("Primary")].to_dict("records"):
            duplicate_by_qb_id.setdefault(str(entry["Source Row ID"]), entry)
    for position, record in enumerate(refined.to_dict("records")):
        duplicate = duplicate_by_qb_id.get(str(record["QuickBooks Row ID"]))
        if duplicate is not None:
            related = _split_reference_ids(duplicate.get("Other Source Row IDs In Group")) + _split_reference_ids(
                duplicate.get("Reference Source Row IDs")
            )
            matched_related = [i for i in related if i in reference_by_id]
            if matched_related:
                references = sorted({reference_by_id[i] for i in matched_related}, key=_match_ref_sort_key)
                index = refined.index[position]
                refined.at[index, "Disposition"] = _duplicate_message(references, str(duplicate.get("Duplicate Basis")))
                refined.at[index, REFERENCED_MATCH_REF_COLUMN] = "; ".join(references)
                refined.at[index, "Reference Basis"] = "Duplicate"
                continue
        if record["Disposition"] != _ALREADY_MATCHED_DISPOSITION:
            continue
        consumed = [
            record_id for record_id in _split_reference_ids(record["Already-Matched Candidate IDs"])
            if record_id in reference_by_id
        ]
        references = sorted({reference_by_id[i] for i in consumed}, key=_match_ref_sort_key)
        if not references:
            continue
        own = qb_refs.get(record["QuickBooks Row ID"], {})
        consumed_refs = [inf_refs[i] for i in consumed if i in inf_refs]
        if all(reference.startswith("G-") for reference in references):
            basis = "Group"
        elif own.get(NORM_PO) and any(c.get(NORM_PO) == own[NORM_PO] for c in consumed_refs):
            basis = "PO"
        elif own.get(NORM_INV) and any(c.get(NORM_INV) == own[NORM_INV] for c in consumed_refs):
            basis = "Invoice"
        else:
            basis = "Record"
        index = refined.index[position]
        refined.at[index, "Disposition"] = _reference_message(references, basis)
        refined.at[index, REFERENCED_MATCH_REF_COLUMN] = "; ".join(references)
        refined.at[index, "Reference Basis"] = basis
    return refined


def apply_referenced_match_references(
    rows: list[dict[str, Any]],
    register: pd.DataFrame,
    candidates: pd.DataFrame,
    qb: pd.DataFrame,
) -> None:
    """Populate "Referenced Match Ref." on every paired row that is NOT
    itself an accepted match but points at a record one already consumed:

    * an unresolved QuickBooks row whose PO/invoice candidate was consumed
      (the evidence recorded by refine_candidates_with_match_references); or
    * a duplicate / potential-duplicate row whose duplicate group contains a
      record that was accepted into a match ("Potential duplicate of Match
      M-042").

    "Referenced Record IDs" keeps the exact records the pointer rests on so
    validate_match_references can prove the cited match contains them. An
    unresolved or duplicate row never receives its own "Match Ref.".
    """
    reference_by_id = _match_reference_by_record_id(register)
    candidate_by_qb = (
        candidates.set_index("QuickBooks Row ID").to_dict("index")
        if not candidates.empty and "QuickBooks Row ID" in candidates.columns else {}
    )
    for row in rows:
        row.setdefault(MATCH_REF_COLUMN, "")
        row[REFERENCED_MATCH_REF_COLUMN] = ""
        row["Referenced Record IDs"] = ""
        row["Reference Basis"] = ""
        if row[MATCH_REF_COLUMN] or not reference_by_id:
            continue
        evidence: list[str] = []
        candidate_basis = ""
        if (
            row.get("Section") in ("02 Unmatched QuickBooks", REFERENCE_HOLD_SECTION)
            and row.get("QB Index") is not None
        ):
            candidate = candidate_by_qb.get(qb.at[row["QB Index"], QB_ID], {})
            candidate_basis = str(candidate.get("Reference Basis") or "")
            if candidate.get(REFERENCED_MATCH_REF_COLUMN) and candidate_basis != "Duplicate":
                evidence.extend(
                    record_id
                    for record_id in _split_reference_ids(candidate.get("Already-Matched Candidate IDs"))
                    if record_id in reference_by_id
                )
            elif candidate_basis != "Duplicate":
                candidate_basis = ""
        evidence.extend(
            record_id for record_id in _split_reference_ids(row.get("Related Source Row IDs"))
            if record_id in reference_by_id
        )
        evidence = list(dict.fromkeys(evidence))
        if not evidence:
            continue
        references = sorted({reference_by_id[i] for i in evidence}, key=_match_ref_sort_key)
        row[REFERENCED_MATCH_REF_COLUMN] = "; ".join(references)
        row["Referenced Record IDs"] = "; ".join(evidence)
        row["Reference Basis"] = candidate_basis or "Duplicate"
        if row["Reference Basis"] == "Duplicate":
            exact = row.get("Section") in {"04 Duplicate QuickBooks", "05 Duplicate Infinium"}
            row["Explanation"] = (
                f"{row['Explanation']} "
                f"{_duplicate_message(references, str(row.get('Duplicate Basis') or ''), exact)}"
            )


def add_duplicate_report_references(report: pd.DataFrame, register: pd.DataFrame) -> pd.DataFrame:
    """Add "Referenced Match Ref." to a duplicate audit report: for a row
    that is not itself in an accepted match, the accepted match(es) that
    another member of its duplicate group belongs to."""
    output = report.copy()
    output[REFERENCED_MATCH_REF_COLUMN] = ""
    reference_by_id = _match_reference_by_record_id(register)
    if output.empty or not reference_by_id or "Duplicate Group ID" not in output.columns:
        return output
    members: dict[Any, list[str]] = {}
    for group_id, source_id in zip(output["Duplicate Group ID"], output["Source Row ID"].astype(str)):
        members.setdefault(group_id, []).append(source_id)
    references: list[str] = []
    for group_id, source_id in zip(output["Duplicate Group ID"], output["Source Row ID"].astype(str)):
        if source_id in reference_by_id:
            references.append("")
            continue
        found = {reference_by_id[m] for m in members[group_id] if m != source_id and m in reference_by_id}
        references.append("; ".join(sorted(found, key=_match_ref_sort_key)))
    output[REFERENCED_MATCH_REF_COLUMN] = references
    return output


def validate_match_references(result: "ReconciliationResult") -> None:
    """Centralized match-reference control, run at the end of every
    reconciliation and again before any workbook is exported. Raises a
    ValueError naming the exact relationship or record at fault.

    One accepted relationship legitimately appears on several worksheets and
    on both the QuickBooks and Infinium side; repetition of the same
    reference for the same relationship is never treated as a duplicate.
    """
    register = result.match_register
    prefix_error = "Match reference control failure"
    relationships: dict[str, dict[str, Any]] = {}
    records = register.to_dict("records") if register is not None and not register.empty else []

    # 7. A reference identifies exactly one relationship.
    seen_keys: dict[str, Any] = {}
    for record in records:
        reference = record["Match Ref."]
        members = f"QuickBooks {record['QuickBooks Row IDs']} / Infinium {record['Infinium Row IDs']}"
        if reference in seen_keys:
            raise ValueError(
                f"{prefix_error}: reference {reference} is assigned to more than one relationship "
                f"({seen_keys[reference]} and {members})."
            )
        seen_keys[reference] = members
        relationships[reference] = {
            "qb": set(_split_reference_ids(record["QuickBooks Row IDs"])),
            "inf": set(_split_reference_ids(record["Infinium Row IDs"])),
            "key": record["Clearance ID"],
        }

    # 8. Format and type: M-### is one-to-one, G-### is grouped, padding is uniform.
    widths: dict[str, set[int]] = {"M": set(), "G": set()}
    for record in records:
        reference = record["Match Ref."]
        matched = _MATCH_REF_PATTERN.match(str(reference))
        if not matched:
            raise ValueError(f"{prefix_error}: {reference!r} is not a valid M-### / G-### reference.")
        prefix, digits = matched.group(1), matched.group(2)
        widths[prefix].add(len(digits))
        grouped = int(record["QuickBooks Row Count"]) > 1 or int(record["Infinium Row Count"]) > 1
        if grouped and prefix != "G":
            raise ValueError(
                f"{prefix_error}: grouped relationship {reference} ({record['QuickBooks Row IDs']} / {record['Infinium Row IDs']}) "
                "was given a one-to-one M-### reference."
            )
        if not grouped and prefix != "M":
            raise ValueError(
                f"{prefix_error}: one-to-one relationship {reference} ({record['QuickBooks Row IDs']} / {record['Infinium Row IDs']}) "
                "was given a grouped G-### reference."
            )
        if (record["Match Type"] == MATCH_TYPE_GROUPED) != grouped:
            raise ValueError(
                f"{prefix_error}: relationship {reference} is labeled {record['Match Type']!r} "
                "but its record counts say otherwise."
            )
    for prefix, found in widths.items():
        if len(found) > 1:
            raise ValueError(f"{prefix_error}: {prefix}-### references use inconsistent zero-padding.")

    # 4. A source record belongs to at most one accepted relationship.
    owner: dict[str, str] = {}
    for reference, relationship in relationships.items():
        for record_id in relationship["qb"] | relationship["inf"]:
            if record_id in owner and owner[record_id] != reference:
                raise ValueError(
                    f"{prefix_error}: record {record_id} is assigned to more than one accepted match "
                    f"({owner[record_id]} and {reference})."
                )
            owner[record_id] = reference

    # 1 & 2. Every accepted match carries its reference, shared by all of its records.
    for group in result.matches:
        if not group.match_ref:
            raise ValueError(
                f"{prefix_error}: accepted match {group.match_id or group.method!r} has no match reference."
            )
        if group.match_ref not in relationships:
            raise ValueError(
                f"{prefix_error}: accepted match reference {group.match_ref} is missing from the match register."
            )
        expected_qb = {str(result.qb_work.at[idx, QB_ID]) for idx in group.qb_rows}
        expected_inf = {str(result.inf_work.at[idx, INF_ID]) for idx in group.inf_rows}
        relationship = relationships[group.match_ref]
        if relationship["qb"] != expected_qb or relationship["inf"] != expected_inf:
            raise ValueError(
                f"{prefix_error}: register rows for {group.match_ref} do not match the accepted "
                "relationship's records."
            )
    if not result.historical_clearances.empty:
        if MATCH_REF_COLUMN not in result.historical_clearances.columns:
            raise ValueError(f"{prefix_error}: historical clearances carry no match reference column.")
        for clearance_id, rows in result.historical_clearances.groupby("Clearance ID", sort=False):
            found = set(rows[MATCH_REF_COLUMN])
            if "" in found:
                raise ValueError(f"{prefix_error}: historical clearance {clearance_id} has no match reference.")
            if len(found) != 1:
                raise ValueError(
                    f"{prefix_error}: records within historical clearance {clearance_id} carry different "
                    f"references ({', '.join(sorted(found))})."
                )
            if next(iter(found)) not in relationships:
                raise ValueError(
                    f"{prefix_error}: historical clearance {clearance_id} cites unknown match "
                    f"{next(iter(found))}."
                )

    matched_sections = {"01 Matched", "01 Matched - Historical Clearance"}
    record_id_frames = {
        ("QB", "Primary"): (result.qb_work, QB_ID),
        ("QB", "Historical"): (result.qb_secondary_work, QB_ID),
        ("INF", "Primary"): (result.inf_work, INF_ID),
        ("INF", "Historical"): (result.inf_secondary_work, INF_ID),
    }
    refs_by_row_key: dict[tuple[str, Any, Any], set[str]] = {}
    for row in result.paired_rows:
        section = row.get("Section")
        reference = row.get(MATCH_REF_COLUMN, "")
        if section in matched_sections:
            if not reference:
                raise ValueError(
                    f"{prefix_error}: accepted {section} row (Match ID {row.get('Match ID')!r}) has no match reference."
                )
            if reference not in relationships:
                raise ValueError(
                    f"{prefix_error}: row shows match reference {reference}, which is not in the match register."
                )
            for side, index_key, scope_key in (
                ("QB", "QB Index", "QB Record Scope"), ("INF", "Infinium Index", "Infinium Record Scope"),
            ):
                if row.get(index_key) is not None:
                    refs_by_row_key.setdefault((side, row[index_key], row.get(scope_key)), set()).add(reference)
                    frame, id_column = record_id_frames.get((side, row.get(scope_key)), (None, None))
                    if frame is not None and row[index_key] in frame.index:
                        record_id = str(frame.at[row[index_key], id_column])
                        if owner.get(record_id) != reference:
                            raise ValueError(
                                f"{prefix_error}: records within relationship {owner.get(record_id)} carry "
                                f"different references ({record_id} shows {reference})."
                            )
        elif reference:
            # 3. Unmatched, duplicate, and review-only rows never carry an accepted-match reference.
            raise ValueError(
                f"{prefix_error}: {section} row (Match ID {row.get('Match ID')!r}) is not an accepted "
                f"match but shows reference {reference}."
            )

    # 2 (cont.). No record shows two different references.
    for (side, index, scope), found in refs_by_row_key.items():
        if len(found) > 1:
            raise ValueError(
                f"{prefix_error}: {side} record {index} ({scope}) carries different references "
                f"({', '.join(sorted(found))})."
            )

    # 3 (cont.). Accepted records never overlap an unmatched or withheld population.
    withheld_qb = set(result.unmatched_qb) | set(result.duplicate_qb_rows)
    withheld_qb |= set(result.duplicate_review_hold_qb_rows) | set(result.amount_variance_review_hold_qb_rows)
    withheld_qb |= set(result.ambiguous_duplicate_qb_rows) | set(result.fuzzy_match_review_hold_qb_rows)
    withheld_qb |= set(result.reference_hold_qb_rows)
    withheld_ids = {str(result.qb_work.at[idx, QB_ID]) for idx in withheld_qb}
    for reference, relationship in relationships.items():
        overlap = relationship["qb"] & withheld_ids
        if overlap:
            raise ValueError(
                f"{prefix_error}: unmatched record {sorted(overlap)[0]} is assigned to accepted match {reference}."
            )

    # 5 & 6. A cited match exists and really contains what the exception points at.
    for row in result.paired_rows:
        cited = _split_reference_ids(row.get(REFERENCED_MATCH_REF_COLUMN, ""))
        if not cited:
            continue
        evidence = set(_split_reference_ids(row.get("Referenced Record IDs", "")))
        for reference in cited:
            if reference not in relationships:
                raise ValueError(
                    f"{prefix_error}: exception (Match ID {row.get('Match ID')!r}) cites match "
                    f"{reference}, which does not exist."
                )
            members = relationships[reference]["qb"] | relationships[reference]["inf"]
            if not evidence & members:
                raise ValueError(
                    f"{prefix_error}: exception (Match ID {row.get('Match ID')!r}) cites {reference}, "
                    "but that match does not contain the record the exception refers to."
                )
