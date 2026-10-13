"""Texas sales-tax rules reference: loading and search, no Streamlit.

The reference is 16 principles, each holding the worked scenarios beneath it (100 in all). The data is the
``taxRules`` collection in ``mongodb/taxRules.json`` (the same documents Atlas holds once ``py -m
mongodb.load_samples`` has run). This module reads nothing from an upload and feeds no workbook: it is guidance to
read, searched and filtered here, and it changes no number anywhere.

Every entry carries a ``status``. ``drafted`` means the citations were assembled from Texas Comptroller and statute
sources but not yet checked by a person; the page says so.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

DATA_PATH = Path(__file__).resolve().parents[2] / "mongodb" / "taxRules.json"
SECTION = "§"
ANY_BUSINESS = "Any business"

_SECTION_NO = re.compile(r"\b(\d{1,3}\.\d{3,4})\b")
_PUB = re.compile(r"Pub\.\s*(\d+-\d+)")


def load_bundled(path: Path = DATA_PATH) -> list[dict]:
    """The rules shipped with the app, in display order."""
    docs = json.loads(path.read_text(encoding="utf-8"))
    return sorted(docs, key=lambda d: d.get("sortOrder", 0))


def load_rules(fetch: Optional[Callable[[], Iterable[dict]]] = None) -> tuple[list[dict], str]:
    """(documents, where they came from). ``fetch`` reads the live collection; any failure or an empty result falls
    back to the bundled copy, so the tab always works."""
    if fetch is not None:
        try:
            docs = sorted(fetch(), key=lambda d: d.get("sortOrder", 0))
            if docs:
                return docs, "database"
        except Exception:  # noqa: BLE001 - the bundled copy is the fallback, whatever went wrong
            pass
    return load_bundled(), "bundled"


def provision_labels(authority: str) -> set[str]:
    """Individual provisions in a citation string, so the provision filter offers one entry per section.

    "Tax Code §§151.0101(13), 151.0047; 34 TAC §3.357; Pub. 94-116" gives Tax Code §151.0101, Tax Code §151.0047,
    34 TAC §3.357 and Comptroller Pub. 94-116.
    """
    labels = set()
    for number in _SECTION_NO.findall(authority):
        kind = "Tax Code" if number.startswith("151.") else "34 TAC"
        labels.add(f"{kind} {SECTION}{number}")
    for number in _PUB.findall(authority):
        labels.add(f"Comptroller Pub. {number}")
    return labels


def all_provisions(docs: list[dict]) -> list[str]:
    labels: set[str] = set()
    for doc in docs:
        for scenario in doc["scenarios"]:
            labels |= provision_labels(scenario["authority"])
    return sorted(labels, key=lambda s: (not s.startswith("Tax Code"), s))


def categories(docs: list[dict]) -> list[str]:
    seen: list[str] = []
    for doc in docs:
        if doc["category"] not in seen:
            seen.append(doc["category"])
    return seen


def treatments(docs: list[dict]) -> list[str]:
    order = ["Generally nontaxable", "Generally taxable", "Depends on the facts"]
    present = {d["usualTreatment"] for d in docs}
    return [t for t in order if t in present]


@dataclass(frozen=True)
class Filters:
    query: str = ""
    categories: tuple[str, ...] = ()
    treatments: tuple[str, ...] = ()
    provisions: tuple[str, ...] = ()
    any_business_only: bool = False

    @property
    def active(self) -> bool:
        return bool(self.query.strip() or self.categories or self.treatments or self.provisions or self.any_business_only)


def _tokens(query: str) -> list[str]:
    return [t for t in re.split(r"\s+", query.casefold().strip()) if t]


def _principle_text(doc: dict) -> str:
    return " ".join([doc["title"], doc["summary"], " ".join(doc["authorities"]), doc["category"],
                     doc["usualTreatment"]]).casefold()


def _scenario_text(doc: dict, scenario: dict) -> str:
    """What a scenario answers for: its own words plus its principle's title (not the principle's summary, so a word
    in the summary does not drag in every scenario beneath it)."""
    return " ".join([doc["title"], scenario["title"], scenario["taxableWhen"], scenario["nontaxableWhen"],
                     scenario["inPractice"], scenario["authority"], scenario["industry"]]).casefold()


def search(docs: list[dict], filters: Filters = Filters()) -> list[dict]:
    """Principles with only the scenarios that match, in display order.

    Every word of the query must appear (case-insensitive). A scenario matches on its own words; only when no scenario
    in a principle matches, but the principle itself does (its title, summary, or citations), are all of its
    scenarios shown, so "forklift" finds the forklift scenario rather than the whole group around it.
    """
    words = _tokens(filters.query)
    out = []
    for doc in docs:
        if filters.categories and doc["category"] not in filters.categories:
            continue
        if filters.treatments and doc["usualTreatment"] not in filters.treatments:
            continue
        eligible = []
        for scenario in doc["scenarios"]:
            if filters.any_business_only and scenario["industry"] != ANY_BUSINESS:
                continue
            if filters.provisions and not (provision_labels(scenario["authority"]) & set(filters.provisions)):
                continue
            eligible.append(scenario)
        if words:
            own = [s for s in eligible if all(w in _scenario_text(doc, s) for w in words)]
            if not own and all(w in _principle_text(doc) for w in words):
                own = eligible
            eligible = own
        if eligible:
            out.append({**doc, "scenarios": eligible, "totalScenarios": len(doc["scenarios"])})
    return out


def totals(results: list[dict]) -> tuple[int, int]:
    """(principles, scenarios) in a result list."""
    return len(results), sum(len(d["scenarios"]) for d in results)


def safe_url(url: Any) -> Optional[str]:
    """Only https links are rendered as links."""
    return url if isinstance(url, str) and url.startswith("https://") else None
