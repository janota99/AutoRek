"""The contractor-bill decision guide: the tree is sound and points at real scenarios."""
import json

from apps.sales_tax import contractor_guide as guide
from apps.sales_tax import tax_rules

DOCS = tax_rules.load_bundled()


def test_the_tree_is_well_formed():
    assert guide.validate() == []


def test_the_guide_has_the_five_outcomes_of_the_diagram():
    assert len(guide.outcomes()) == 5
    tones = sorted(o["tone"] for o in guide.outcomes())
    assert tones == ["free", "free", "mixed", "tax", "tax"]


def test_every_outcome_points_at_scenarios_that_exist():
    numbers = {s["no"] for d in DOCS for s in d["scenarios"]}
    for outcome in guide.outcomes():
        assert outcome["scenarios"] and set(outcome["scenarios"]) <= numbers, outcome["headline"]


def test_references_carry_the_scenario_title_and_authority():
    tree = guide.with_references(DOCS)
    assert all("references" not in o for o in guide.outcomes())   # the source tree is not modified

    def collect(node):
        if "outcome" in node:
            yield node["outcome"]
        for opt in node.get("options", []):
            yield from collect(opt["next"])

    for outcome in collect(tree):
        assert outcome["references"] and all(r["title"] and r["authority"] for r in outcome["references"])


def test_the_json_is_safe_inside_a_script_block():
    payload = guide.tree_json(DOCS)
    assert "<" not in payload
    assert json.loads(payload)["question"] == "What did the contractor work on?"


def test_the_outline_lists_every_question_and_option():
    outline = guide.outline_html()
    for label in ("Movable equipment", "Commercial building or structure", "One lump-sum charge",
                  "Labor and materials separated", "No: new addition or construction"):
        assert label.replace("&", "&amp;") in outline
    assert "Does the equipment qualify for the manufacturing exemption?" in outline
