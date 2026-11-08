"""Material identification: which of the ~30 raw materials a name, alias or product ID means.

Operations' reports name materials in whatever spelling their system uses. The match is by
normalized name, like the FY2026/FY2027 inventory workbooks: case, spaces and punctuation are
ignored, so ``Low 32 Film``, ``LOW32-FILM`` and ``low32 film`` are one material.

A name resolves to a material only when it is unambiguous. If one spelling could mean two
materials (``W0082`` is the code of both the blue and the natural caps), it is reported as
ambiguous and never guessed. The person's own saved spellings win over the built-in ones.

Pure apart from the small saved-spellings file (``material_aliases.json`` next to this module,
or ``FIFO_ALIASES_PATH``). No Streamlit.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from .known_spellings import KNOWN_SPELLINGS, PRODUCT_IDS
from .user_inputs import PRODUCTS

ALIASES_PATH = Path(os.environ.get(
    "FIFO_ALIASES_PATH", Path(__file__).resolve().parent / "material_aliases.json"
))

OK, UNMAPPED, AMBIGUOUS = "ok", "unmapped", "ambiguous"

_PRODUCT_ID = re.compile(r"^ITM[\s\-_]?0*(\d{1,3})$", re.IGNORECASE)


def normalize_name(value) -> str:
    """Upper-case letters and digits only."""
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


# Supplies and parts operations logs or counts that are not FIFO materials. Matched only after a name
# fails to identify a material, so a known material is never dropped by this list.
_NON_INVENTORY = ("PALLETWRAP", "SLIPSHEET", "PAPER", "PARTS", "SUPPLIES", "FLUTE", "STRAPPING", "STRETCHWRAP",
                  "TOWEL", "GLOVES", "CLEANER", "CHEMICAL", "ACID", "SQUEEGEE", "BROOM", "MOTOR", "HARDWARE",
                  "ANTIFATIGUE", "SMOKEURN")


def is_non_inventory(value) -> bool:
    """True for names such as 'Pallet Wrap (24)' or 'Slip Sheets': not a raw material, so never an exception."""
    key = normalize_name(value)
    return bool(key) and any(token in key for token in _NON_INVENTORY)


# Operations words some things differently from period to period. These are naming equivalences only, applied
# when comparing material names: a case is a pack, and film is rolls. They never merge two materials by
# themselves (every other word and number in the name must still agree: size, customer, item number) and they
# imply no unit-of-measure conversion: quantities and costs keep the meaning they were reported with.
_EQUIVALENT_TERMS = {
    "CASE": "PACK", "CASES": "PACK", "PACK": "PACK", "PACKS": "PACK", "PK": "PACK", "PKS": "PACK",
    "ROLL": "FILM", "ROLLS": "FILM", "FILM": "FILM", "FILMS": "FILM",
}


def match_key(value) -> str:
    """The key material names are compared by: letters and digits, words and numbers kept apart, equivalent
    terms made one ("Lowes 24 (Rolls)" and "LOW24 FILM" differ only by the customer's own spelling, which the
    spelling table handles; "PP 24 Rolls" and "PP24 Film" are the same key). Used for matching only."""
    tokens = re.findall(r"[A-Z]+|\d+", str(value).upper())
    mapped: list[str] = []
    for token in tokens:
        token = _EQUIVALENT_TERMS.get(token, token)
        if not mapped or mapped[-1] != token or not token.isalpha():
            mapped.append(token)                     # "film rolls" is one word of meaning, not two
        # an immediately repeated equivalent term adds nothing
    return "".join(mapped)


def _blank(value) -> bool:
    return value is None or (not isinstance(value, str) and pd.isna(value)) or str(value).strip() == ""


def _as_alias(value) -> Optional[int]:
    """The integer alias a cell holds (2, 2.0, '2'), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int,)):
        return int(value)
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = str(value).strip()
    return int(text) if re.fullmatch(r"\d{1,3}", text) else None


@dataclass(frozen=True)
class Match:
    alias: Optional[int]
    status: str            # OK, UNMAPPED or AMBIGUOUS
    detail: str = ""       # how it matched, or why it did not


def _builtin_keys(products: dict[int, str]) -> dict[str, set[int]]:
    """Every spelling a product can be written as: full name, name without its W-code, the code."""
    keys: dict[str, set[int]] = {}
    for alias, name in products.items():
        spellings = {name}
        if " - " in name:
            code, rest = name.split(" - ", 1)
            spellings.update({rest, code})
        for spelling in spellings:
            key = match_key(spelling)
            if key:
                keys.setdefault(key, set()).add(alias)
    return keys


class MaterialMatcher:
    def __init__(self, products: dict[int, str] = None, saved: dict[str, int] = None):
        self.products = dict(PRODUCTS if products is None else products)
        self._builtin = _builtin_keys(self.products)
        self._known: dict[str, set[int]] = {}
        for spelling, alias in KNOWN_SPELLINGS.items():
            if alias in self.products and match_key(spelling):
                self._known.setdefault(match_key(spelling), set()).add(alias)
        self._saved: dict[str, int] = {}
        for spelling, alias in (saved or {}).items():
            key = match_key(spelling)
            if key and int(alias) in self.products:
                self._saved[key] = int(alias)

    def match(self, value) -> Match:
        if _blank(value):
            return Match(None, UNMAPPED, "blank")
        alias = _as_alias(value)
        if alias is not None and alias in self.products:
            return Match(alias, OK, "alias")
        text = str(value).strip()
        key = match_key(text)
        if not key:
            return Match(None, UNMAPPED, "no letters or digits")
        if key in self._saved:
            return Match(self._saved[key], OK, "saved spelling")
        found = _PRODUCT_ID.match(text)
        if found:
            alias = PRODUCT_IDS.get(f"ITM-{int(found.group(1)):02d}")
            if alias in self.products:
                return Match(alias, OK, "product ID")
        known = self._known.get(key, set())
        if len(known) == 1:
            return Match(next(iter(known)), OK, "known spelling")
        if len(known) > 1:
            names = ", ".join(f"{a} {self.products[a]}" for a in sorted(known))
            return Match(None, AMBIGUOUS, f"could be {names}")
        candidates = self._builtin.get(key, set())
        if len(candidates) == 1:
            return Match(next(iter(candidates)), OK, "name")
        if len(candidates) > 1:
            names = ", ".join(f"{a} {self.products[a]}" for a in sorted(candidates))
            return Match(None, AMBIGUOUS, f"could be {names}")
        return Match(None, UNMAPPED, "not a known material")

    def match_first(self, values) -> Match:
        """The first non-blank value that identifies a material; if none does, the first problem seen.

        A row can carry several identifying cells (alias, description, item text). A cell that
        resolves wins. Two cells that resolve to different materials are a conflict, never a pick.
        """
        resolved: list[Match] = []
        problem: Optional[Match] = None
        for value in values:
            if _blank(value):
                continue
            result = self.match(value)
            if result.status == OK:
                resolved.append(result)
            elif problem is None or (problem.status == UNMAPPED and result.status == AMBIGUOUS):
                problem = result
        distinct = {m.alias for m in resolved}
        if len(distinct) > 1:
            return Match(None, AMBIGUOUS, "its cells name different materials: "
                         + ", ".join(f"{a} {self.products[a]}" for a in sorted(distinct)))
        if resolved:
            return resolved[0]
        return problem or Match(None, UNMAPPED, "blank")

    def suggestions(self, value, limit: int = 3) -> list[str]:
        """Likely materials for an unrecognized name. Display only; a suggestion is never applied."""
        import difflib
        key = normalize_name(value)
        scored = []
        for alias, name in self.products.items():
            ratio = difflib.SequenceMatcher(None, key, normalize_name(name)).ratio()
            scored.append((ratio, alias))
        scored.sort(reverse=True)
        return [f"{alias} {self.products[alias]}" for ratio, alias in scored[:limit] if ratio >= 0.5]


# ------------------------------------------------------------------
# Saved spellings
# ------------------------------------------------------------------

def load_saved_aliases() -> dict[str, int]:
    """The person's saved spellings, {spelling: alias}. A missing or unreadable file is no spellings."""
    try:
        raw = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in raw.items() if int(v) in PRODUCTS}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def save_aliases(additions: dict[str, int]) -> dict[str, int]:
    """Add spellings to the saved file (atomic write, read back). Returns everything now saved."""
    merged = load_saved_aliases()
    for spelling, alias in additions.items():
        if int(alias) not in PRODUCTS:
            raise ValueError(f"Alias {alias} is not a known material.")
        if not normalize_name(spelling):
            raise ValueError(f"{spelling!r} has no letters or digits to match on.")
        merged[str(spelling).strip()] = int(alias)
    tmp = ALIASES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(ALIASES_PATH)
    if load_saved_aliases() != merged:
        raise IOError(f"Saved spellings in {ALIASES_PATH} failed read-back verification.")
    return merged


def current_matcher() -> MaterialMatcher:
    return MaterialMatcher(PRODUCTS, load_saved_aliases())
