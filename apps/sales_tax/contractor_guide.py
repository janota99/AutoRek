"""The contractor-bill decision guide: "Should we pay Texas sales tax on this contractor bill?"

A small decision tree (movable equipment or commercial building, then repair or new construction, then how the
contract bills the work) that ends in a plain statement of the usual result. No Streamlit, no calculation, and it
changes no number: it is guidance that points at the scenarios already in the rules reference.

The tree here is the data; ``contractor_guide.js`` draws it as buttons, and ``outline_html`` draws the same tree as
a nested list so it can be read or printed without the script.
"""
from __future__ import annotations

import html
import json
from typing import Any

# An option leads either to another question or to an outcome.
#   question: {"question": str, "options": [{"label": str, "next": node}, ...]}
#   outcome:  {"outcome": {"tone": "free" | "tax" | "mixed", "headline": str, "detail": str, "scenarios": [int, ...]}}
TREE: dict[str, Any] = {
    "question": "What did the contractor work on?",
    "options": [
        {
            "label": "Movable equipment",
            "next": {
                "question": "Does the equipment qualify for the manufacturing exemption?",
                "options": [
                    {"label": "Yes", "next": {"outcome": {
                        "tone": "free",
                        "headline": "Generally no tax on qualifying repair charges.",
                        "detail": "Keep support showing the equipment's qualifying manufacturing use.",
                        "scenarios": [95, 58]}}},
                    {"label": "No", "next": {"outcome": {
                        "tone": "tax",
                        "headline": "Generally yes. Tax applies to the full equipment repair charge.",
                        "detail": "That includes labor and parts.",
                        "scenarios": [95, 94]}}},
                ],
            },
        },
        {
            "label": "Commercial building or structure",
            "next": {
                "question": "Is it a repair or remodel of an existing building?",
                "options": [
                    {"label": "Yes", "next": {"outcome": {
                        "tone": "tax",
                        "headline": "Yes. Tax generally applies to the full charge.",
                        "detail": "That includes labor and materials, even when the contractor lists them separately.",
                        "scenarios": [82, 84, 83]}}},
                    {"label": "No: new addition or construction", "next": {
                        "question": "How does the contract bill the work?",
                        "options": [
                            {"label": "One lump-sum charge", "next": {"outcome": {
                                "tone": "free",
                                "headline": "No sales tax is separately charged on the lump-sum bill.",
                                "detail": "The contractor pays tax on the taxable inputs it buys.",
                                "scenarios": [88, 87]}}},
                            {"label": "Labor and materials separated", "next": {"outcome": {
                                "tone": "mixed",
                                "headline": "No tax on construction labor.",
                                "detail": "Tax generally applies to materials and certain separately identified services.",
                                "scenarios": [89, 87]}}},
                        ],
                    }},
                ],
            },
        },
    ],
}

TONES = {"free", "tax", "mixed"}
ROOT_QUESTION = "Should we pay Texas sales tax on this contractor bill?"


def _walk(node: dict, depth: int = 0):
    yield node, depth
    for option in node.get("options", []):
        yield from _walk(option["next"], depth + 1)


def outcomes(tree: dict = TREE) -> list[dict]:
    return [node["outcome"] for node, _ in _walk(tree) if "outcome" in node]


def validate(tree: dict = TREE) -> list[str]:
    """Problems with the tree's shape (an empty list means it is sound)."""
    problems = []
    for node, _ in _walk(tree):
        if "outcome" in node:
            out = node["outcome"]
            if out.get("tone") not in TONES:
                problems.append(f"outcome {out.get('headline')!r}: unknown tone {out.get('tone')!r}")
            if not out.get("headline") or not out.get("scenarios"):
                problems.append(f"outcome {out.get('headline')!r}: needs a headline and scenarios")
        else:
            if not node.get("question"):
                problems.append("a question node has no question")
            options = node.get("options", [])
            if len(options) < 2:
                problems.append(f"question {node.get('question')!r}: needs at least two options")
            labels = [o["label"] for o in options]
            if len(set(labels)) != len(labels):
                problems.append(f"question {node.get('question')!r}: duplicate option labels")
    return problems


def with_references(docs: list[dict], tree: dict = TREE) -> dict:
    """A copy of the tree whose outcomes carry the title and authority of the scenarios they point at."""
    by_no = {s["no"]: s for d in docs for s in d["scenarios"]}

    def convert(node: dict) -> dict:
        if "outcome" in node:
            out = dict(node["outcome"])
            out["references"] = [
                {"no": n, "title": by_no[n]["title"], "authority": by_no[n]["authority"]}
                for n in out["scenarios"] if n in by_no
            ]
            return {"outcome": out}
        return {"question": node["question"],
                "options": [{"label": o["label"], "next": convert(o["next"])} for o in node["options"]]}

    return convert(tree)


def tree_json(docs: list[dict]) -> str:
    """The tree as JSON that is safe inside an HTML script block (every "<" escaped)."""
    return json.dumps(with_references(docs), ensure_ascii=False).replace("<", "\\u003c")


def outline_html(tree: dict = TREE) -> str:
    """The same tree as a nested list, readable without the script."""
    def render(node: dict) -> str:
        if "outcome" in node:
            o = node["outcome"]
            return f'<span class="cg-oo">{html.escape(o["headline"])} {html.escape(o["detail"])}</span>'
        items = "".join(f"<li><b>{html.escape(opt['label'])}</b> &rarr; {render(opt['next'])}</li>"
                        for opt in node["options"])
        return f'<span class="cg-oq">{html.escape(node["question"])}</span><ul>{items}</ul>'

    return f'<ul class="cg-outline"><li>{render(tree)}</li></ul>'
