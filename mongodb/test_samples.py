"""The sample documents are valid, and they match the code they describe.

Run with `py -m pytest mongodb` (or as part of `py -m pytest`).
"""

import copy

from mongodb.check_samples import check_all, load_documents, load_schema, validate

from apps.sales_page import TIERS
from shared.layout import APPS


def test_every_sample_document_matches_its_schema():
    assert {name: found for name, found in check_all().items() if found} == {}


def test_each_collection_has_several_documents():
    for name in ("reviews", "plans", "tools"):
        assert len(load_documents(name)) >= 3, name


def test_the_samples_show_each_type_the_assignment_names():
    """String, number, Boolean and null all appear, so the structure is demonstrated."""
    reviews = load_documents("reviews")
    assert any(r["role"] is None for r in reviews)                      # null
    assert any(r["submittedAt"] is None for r in reviews)               # null (sample reviews)
    assert {r["recommend"] for r in reviews} == {True, False}           # Boolean
    assert all(isinstance(r["rating"], int) for r in reviews)           # number
    assert all(isinstance(r["name"], str) for r in reviews)             # string
    assert any(p["monthlyPriceUsd"] is None for p in load_documents("plans"))  # null = custom quote


def test_schema_rejects_the_mistakes_it_exists_to_catch():
    schema = load_schema("reviews")
    good = load_documents("reviews")[2]
    assert validate(good, schema) == []
    for field, bad in [
        ("rating", 6),            # outside 1-5
        ("rating", 4.5),          # not a whole number
        ("rating", "5"),          # a string where JavaScript sends a number
        ("recommend", "yes"),     # the form's string, not a Boolean
        ("name", "A"),            # shorter than 2 characters
        ("submittedAt", "2026-09-14"),  # a string, not a date
    ]:
        doc = copy.deepcopy(good)
        doc[field] = bad
        assert validate(doc, schema), (field, bad)
    extra = copy.deepcopy(good)
    extra["unexpected"] = 1
    assert validate(extra, schema)
    missing = copy.deepcopy(good)
    del missing["rating"]
    assert validate(missing, schema)


def test_plans_match_the_plans_page():
    docs = {d["_id"]: d for d in load_documents("plans")}
    assert list(docs) == [t.id for t in TIERS]
    for tier in TIERS:
        d = docs[tier.id]
        assert d["name"] == tier.name
        assert d["positioning"] == tier.positioning
        assert d["audience"] == tier.audience
        assert d["monthlyPriceUsd"] == tier.monthly_price
        assert d["annualPriceUsd"] == (None if tier.monthly_price is None else tier.monthly_price * 10)
        assert d["inherits"] == (tier.inherits or None)
        assert d["features"] == list(tier.features)
        assert d["featured"] is tier.featured
        assert d["requiresQuote"] is (tier.monthly_price is None)
        assert d["ctaLabel"] == tier.action
        assert d["toolIds"] == list(tier.tool_ids)


def test_tools_match_the_dashboard_cards():
    docs = load_documents("tools")
    assert [d["_id"] for d in docs] == [a.url_path for a in APPS]
    tier_of = {tool: t.id for t in TIERS for tool in t.tool_ids}
    for order, (doc, app) in enumerate(zip(docs, APPS), start=1):
        assert doc["title"] == app.title
        assert doc["route"] == "/" + app.url_path
        assert doc["summary"] == app.summary
        assert doc["actionLabel"] == app.action
        assert doc["badge"] == (app.badge or None)
        assert doc["isPrototype"] is bool(app.badge)
        assert doc["inputs"] == list(app.inputs)
        assert doc["outputs"] == list(app.outputs)
        assert doc["sortOrder"] == order
        assert doc["minimumPlanId"] == tier_of[app.url_path]
