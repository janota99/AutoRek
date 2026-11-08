"""Naming equivalences (case = pack, film = rolls): matching only, never a merge by themselves, never a unit conversion."""
from collections import defaultdict
from datetime import date, datetime

import pandas as pd
import pytest

from apps.fifo_inventory.intake import PeriodWindow, prepare_period_receipts, read_ending_report
from apps.fifo_inventory.known_spellings import KNOWN_SPELLINGS
from apps.fifo_inventory.materials import AMBIGUOUS, OK, UNMAPPED, MaterialMatcher, match_key
from apps.fifo_inventory.user_inputs import PRODUCTS

WINDOW = PeriodWindow(end=date(2026, 8, 29), start=date(2026, 8, 2), source="calendar")


@pytest.fixture
def matcher():
    return MaterialMatcher(PRODUCTS, {})


# --- film and rolls ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("spelling, alias", [
    ("PP 24 Film", 14), ("PP24 Rolls", 14), ("pp 24 (rolls)", 14), ("PP24-FILM", 14), ("PP 24 (Rolls)", 14),
    ("PP32 (Rolls)", 15), ("PP32 Film", 15), ("PP 40 Rolls", 16),
    ("Lowes 24 (Rolls)", 17), ("LOWES 24 FILM", 17), ("Lowes 32 Rolls", 18), ("Lowes 40 Film", 19),
    ("Food King 24 (Rolls)", 20), ("Food King 24 Film", 20), ("Food King 40 Rolls", 21),
    ("Food Club 24 (Rolls)", 22), ("Food Club 24 Film", 22), ("Food Club 32 Rolls", 23), ("Food Club 40 Film", 24),
    ("Juniors 24(Rolls)", 25), ("Juniors 24 Film", 25), ("Plains 24 (Rolls)", 26), ("PLAINS 24 FILM", 26),
    ("Springhouse 24 (Rolls)", 27), ("TNT 24 Rolls", 28), ("TNT 24 Film", 28), ("Allsups 24 (Rolls)", 29),
    ("Allsups 24 Film", 29),
])
def test_film_and_rolls_resolve_to_the_same_canonical_material(matcher, spelling, alias):
    result = matcher.match(spelling)
    assert (result.status, result.alias) == (OK, alias)


@pytest.mark.parametrize("a, b", [("PP 24 Film", "PP 24 Rolls"), ("Lowes 32 Film", "Lowes 32 (Rolls)"),
                                  ("Food Club 40 Rolls", "Food Club 40 Film"), ("TNT 24 Film", "TNT 24 Rolls")])
def test_the_two_words_are_interchangeable_for_the_same_material(matcher, a, b):
    assert matcher.match(a).alias == matcher.match(b).alias is not None


def test_every_size_and_customer_still_distinguishes_materials(matcher):
    sizes = {matcher.match(f"Lowes {n} Film").alias for n in (24, 32, 40)}
    assert sizes == {17, 18, 19}
    customers = {matcher.match(f"{c} 24 Rolls").alias for c in ("PP", "Lowes", "Food King", "Food Club", "Plains", "TNT")}
    assert customers == {14, 17, 20, 22, 26, 28}
    assert matcher.match("PP 24 Rolls").alias != matcher.match("PP 40 Rolls").alias


@pytest.mark.parametrize("vague", ["Film", "Rolls", "FILM ROLLS", "Lowes Film", "Lowes Rolls", "24 Film", "Plains Film",
                                   "Labels Film", "Pallet Film", "Film 99"])
def test_the_words_alone_never_pick_a_material(matcher, vague):
    result = matcher.match(vague)
    assert result.status == UNMAPPED and result.alias is None


def test_film_is_not_labels_and_labels_are_not_film(matcher):
    assert matcher.match("Lowes").alias == 6                       # the customer's labels
    assert matcher.match("Lowes Labels").alias == 6
    assert matcher.match("Lowes 24 Film").alias == 17              # that customer's 24-size film
    assert matcher.match("Plains").alias == 10 and matcher.match("Plains 24 Rolls").alias == 26


# --- cases and packs ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("spelling", ["Cases of Labler Glue", "Packs of Labler Glue", "Pack of Labler Glue",
                                      "case of labler glue", "PK of Labler Glue"])
def test_cases_and_packs_are_one_word_for_the_same_glue(matcher, spelling):
    assert (matcher.match(spelling).status, matcher.match(spelling).alias) == (OK, 30)


@pytest.mark.parametrize("name", ["PP 24 Cases", "PP 24 Packs", "Lowes 40 Cases", "Food Club 24 Pack", "Cases", "Packs",
                                  "Cases of Film", "Packs of Labels", "24 Cases"])
def test_cases_and_packs_never_turn_other_products_into_a_material(matcher, name):
    assert matcher.match(name).status == UNMAPPED


def test_cases_of_film_are_not_film_and_packs_are_not_rolls(matcher):
    assert match_key("PP 24 Cases") == match_key("PP 24 Packs") != match_key("PP 24 Rolls")
    assert matcher.match("PP 24 Rolls").alias == 14 and matcher.match("PP 24 Packs").alias is None
    assert match_key("LOWES 24PK FILM") != match_key("Lowes 24 Film")        # a pack-size word is not dropped


def test_a_different_identifier_keeps_cases_apart(matcher):
    assert matcher.match("Cases of Labler Glue").alias == 30
    assert matcher.match("Cases of Other Glue").status == UNMAPPED


# --- no collisions, ever -------------------------------------------------------------------------------------

def test_no_two_materials_share_a_canonical_spelling():
    seen = defaultdict(set)
    for spelling, alias in KNOWN_SPELLINGS.items():
        seen[match_key(spelling)].add(alias)
    for alias, name in PRODUCTS.items():
        seen[match_key(name)].add(alias)
        if " - " in name:
            seen[match_key(name.split(" - ", 1)[1])].add(alias)
    assert {k: v for k, v in seen.items() if len(v) > 1} == {}


def test_the_only_ambiguous_spelling_is_the_shared_w_code_and_it_still_is(matcher):
    result = matcher.match("W0082")
    assert result.status == AMBIGUOUS and "BLUE CAPS" in result.detail and "NATURAL CAPS" in result.detail


def test_a_saved_spelling_uses_the_same_equivalences():
    saved = MaterialMatcher(PRODUCTS, {"Special 24 Rolls": 14})
    assert saved.match("special 24 film").alias == 14


# --- no unit conversion: quantities and costs keep their reported meaning ---------------------------------------

def _receipts(item, qty, total):
    return pd.DataFrame([{"Item": item, "Delivery Date": datetime(2026, 8, 5), "Quantity": qty, "Total Cost": total,
                          "UOM": "rolls"}])


@pytest.mark.parametrize("item", ["PP 24 Film", "PP 24 (Rolls)", "PP24 ROLLS"])
def test_the_name_changes_nothing_about_quantity_or_cost(matcher, item):
    valid, errors, *_ = prepare_period_receipts(_receipts(item, 12, 345.67), matcher, WINDOW, 12)
    assert errors == [] and valid["PRODUCT ALIAS"].tolist() == [14]
    assert valid["QUANTITY"].tolist() == [12.0] and valid["PRICE"].tolist() == [345.67]     # as reported, no conversion
    assert valid["UOM"].tolist() == ["rolls"]


def test_a_cases_count_is_the_number_reported_not_a_converted_one(matcher):
    ending = pd.DataFrame({"Item": ["Packs of Labler Glue", "Cases of Labler Glue"], "Ending Quantity": [39, 41]})
    report = read_ending_report(ending, matcher, 12)
    # two spellings of one material are added and said out loud; the numbers are exactly the reported ones
    assert report.grid.set_index("PRODUCT ALIAS")["12"][30] == 80
    assert any("Packs of Labler Glue 39 + Cases of Labler Glue 41 = 80" in n for n in report.notes)


def test_one_spelling_alone_is_not_scaled(matcher):
    report = read_ending_report(pd.DataFrame({"Item": ["Cases of Labler Glue"], "Ending Quantity": [39]}), matcher, 12)
    assert report.grid.set_index("PRODUCT ALIAS")["12"][30] == 39
