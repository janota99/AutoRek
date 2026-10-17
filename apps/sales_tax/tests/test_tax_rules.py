"""The Texas tax rules reference: the bundled data is sound, and search and filters behave."""
import re

from apps.sales_tax import tax_rules
from apps.sales_tax.tax_rules import ANY_BUSINESS, SECTION, Filters, provision_labels, search, totals

DOCS = tax_rules.load_bundled()


def _scenarios():
    return [s for d in DOCS for s in d["scenarios"]]


def test_sixteen_principles_hold_all_one_hundred_scenarios_once():
    assert len(DOCS) == 16
    numbers = sorted(s["no"] for s in _scenarios())
    assert numbers == list(range(1, 101))


def test_every_scenario_names_its_authority_and_every_source_resolves_to_an_https_link():
    for doc in DOCS:
        keys = {s["key"] for s in doc["sources"]}
        for src in doc["sources"]:
            assert src["url"].startswith("https://"), src
        for s in doc["scenarios"]:
            assert s["authority"].strip()
            assert set(s["sourceKeys"]) <= keys, (doc["_id"], s["no"])
        assert doc["authorities"], doc["_id"]


def test_entries_start_drafted_and_unverified_until_a_person_checks_them():
    assert {d["status"] for d in DOCS} == {"drafted"}
    assert all(d["lastVerified"] is None for d in DOCS)


def test_no_text_identifies_the_company_the_guide_was_first_written_for():
    pattern = re.compile(r"\b(company|member[- ]store|vault|banana|this group|group's|described company)\b", re.I)
    for doc in DOCS:
        for field in ("title", "summary"):
            assert not pattern.search(doc[field]), (doc["_id"], field)
        for s in doc["scenarios"]:
            for field in ("title", "taxableWhen", "nontaxableWhen", "inPractice"):
                assert not pattern.search(s[field]), (s["no"], field, s[field])


def test_the_unfiltered_search_returns_everything_in_display_order():
    results = search(DOCS)
    assert totals(results) == (16, 100)
    assert [d["sortOrder"] for d in results] == sorted(d["sortOrder"] for d in results)


def test_search_requires_every_word_and_ignores_case():
    wide = totals(search(DOCS, Filters(query="ripening")))[1]
    narrow = totals(search(DOCS, Filters(query="ripening equipment")))[1]
    assert wide >= narrow > 0
    assert totals(search(DOCS, Filters(query="RIPENING")))[1] == wide
    assert totals(search(DOCS, Filters(query="ripening zzzzzz-nothing")))[1] == 0


def test_a_search_can_find_a_scenario_by_what_it_says():
    results = search(DOCS, Filters(query="forklift"))
    assert any(s["no"] == 51 for d in results for s in d["scenarios"])


def test_filters_narrow_by_category_treatment_and_provision():
    cat = search(DOCS, Filters(categories=("Packaging and charges",)))
    assert {d["category"] for d in cat} == {"Packaging and charges"}
    treat = search(DOCS, Filters(treatments=("Generally nontaxable",)))
    assert {d["usualTreatment"] for d in treat} == {"Generally nontaxable"}
    prov = search(DOCS, Filters(provisions=(f"Tax Code {SECTION}151.314",)))
    assert prov and all("151.314" in s["authority"] for d in prov for s in d["scenarios"])


def test_any_business_only_hides_the_food_specific_scenarios():
    everything = totals(search(DOCS))[1]
    general = search(DOCS, Filters(any_business_only=True))
    assert 0 < totals(general)[1] < everything
    assert all(s["industry"] == ANY_BUSINESS for d in general for s in d["scenarios"])


def test_provisions_are_listed_one_per_section():
    assert provision_labels("Tax Code \u00a7\u00a7151.0101(13), 151.0047; 34 TAC \u00a73.357; Pub. 94-116") == {
        f"Tax Code {SECTION}151.0101", f"Tax Code {SECTION}151.0047", f"34 TAC {SECTION}3.357", "Comptroller Pub. 94-116"}
    labels = tax_rules.all_provisions(DOCS)
    assert f"Tax Code {SECTION}151.318" in labels and f"34 TAC {SECTION}3.300" in labels


def test_the_loader_falls_back_to_the_bundled_copy():
    def broken():
        raise RuntimeError("cluster unreachable")

    assert tax_rules.load_rules(broken)[1] == "bundled"
    assert tax_rules.load_rules(lambda: [])[1] == "bundled"
    live = [{**DOCS[0], "sortOrder": 1}]
    assert tax_rules.load_rules(lambda: live) == (live, "database")


def test_only_https_links_are_rendered():
    assert tax_rules.safe_url("https://comptroller.texas.gov/x") == "https://comptroller.texas.gov/x"
    assert tax_rules.safe_url("javascript:alert(1)") is None
    assert tax_rules.safe_url(None) is None


def test_a_word_found_in_a_scenario_returns_that_scenario_not_the_whole_group():
    results = search(DOCS, Filters(query="forklift"))
    numbers = [s["no"] for d in results for s in d["scenarios"]]
    assert 51 in numbers and len(numbers) < 7


def test_a_phrase_found_only_in_a_principle_returns_its_scenarios():
    # "carved out" appears in the principle title, not in any single scenario
    results = search(DOCS, Filters(query="carved out"))
    group = next(d for d in results if d["_id"] == "candy-drinks-snacks")
    assert len(group["scenarios"]) == group["totalScenarios"] == 5


# --- a person's own rules ------------------------------------------------------------------------------------------

import pytest


def _entry(**over):
    base = {"title": "Equipment rentals", "category": "Equipment and supplies", "treatment": "Depends on the facts",
            "summary": "Rentals of movable equipment follow the rule for the underlying property.",
            "taxableWhen": "The renter uses it for general business.", "nontaxableWhen": "", "inPractice": "",
            "authority": "Tax Code \u00a7151.007", "url": "https://example.com/source"}
    base.update(over)
    return base


def test_a_valid_entry_becomes_a_searchable_document_marked_as_the_visitors_own():
    entry = tax_rules.clean_custom_entry(_entry())
    doc = tax_rules.custom_docs([entry])[0]
    assert doc["custom"] is True and doc["status"] == "custom" and doc["scenarios"][0]["no"] == "C1"
    assert doc["scenarios"][0]["nontaxableWhen"] == "Not stated."
    found = search(DOCS + [doc], Filters(query="equipment rentals"))
    assert any(d.get("custom") for d in found)
    assert any(d.get("custom") for d in search(DOCS + [doc], Filters(categories=("Equipment and supplies",))))


@pytest.mark.parametrize("over, fragment", [
    ({"title": "ab"}, "title"),
    ({"summary": "short"}, "summary"),
    ({"url": "http://example.com"}, "https"),
    ({"url": "javascript:alert(1)"}, "https"),
    ({"category": "Not a category"}, "category"),
    ({"treatment": "Maybe"}, "treatment"),
    ({"authority": "x" * 201}, "authority"),
])
def test_bad_entries_are_refused_with_a_reason(over, fragment):
    with pytest.raises(ValueError) as exc:
        tax_rules.clean_custom_entry(_entry(**over))
    assert fragment in str(exc.value)


def test_unknown_fields_are_dropped_not_stored():
    entry = tax_rules.clean_custom_entry({**_entry(), "script": "<script>alert(1)</script>", "_id": "hacked"})
    assert set(entry) == set(tax_rules.CUSTOM_FIELDS)


def test_export_then_import_round_trips_and_revalidates():
    entries = [tax_rules.clean_custom_entry(_entry()), tax_rules.clean_custom_entry(_entry(title="Second rule"))]
    assert tax_rules.import_entries(tax_rules.export_entries(entries)) == entries


def test_import_refuses_other_files_bad_rules_and_too_many():
    with pytest.raises(ValueError):
        tax_rules.import_entries("not json")
    with pytest.raises(ValueError):
        tax_rules.import_entries('{"format": "something-else", "rules": []}')
    bad = tax_rules.export_entries([_entry(url="http://insecure.example")])
    with pytest.raises(ValueError):
        tax_rules.import_entries(bad)
    many = tax_rules.export_entries([_entry()] * (tax_rules.MAX_CUSTOM + 1))
    with pytest.raises(ValueError):
        tax_rules.import_entries(many)
    ok = tax_rules.export_entries([_entry()] * 3)
    with pytest.raises(ValueError):
        tax_rules.import_entries(ok, existing=tax_rules.MAX_CUSTOM - 2)


def test_a_visitors_text_is_escaped_when_drawn():
    from apps.sales_tax.tax_rules_ui import _scenario_html
    doc = tax_rules.custom_docs([tax_rules.clean_custom_entry(_entry(title="<img src=x onerror=alert(1)> rule"))])[0]
    drawn = _scenario_html(doc["scenarios"][0])
    assert "<img" not in drawn and "&lt;img" in drawn
