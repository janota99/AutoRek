"""The Build your template step: settings that need no file, the order of the page, and a library that cannot stall it."""
import pytest
from streamlit.testing.v1 import AppTest

from apps.recon.custom import ui
from apps.recon.custom.spec import (
    PATTERNS, ColumnPair, ReconSpec, build_spec, builder_problems,
)

BASE = ReconSpec(name="Starter", label_a="Dataset A", label_b="Dataset B")


# --- the settings logic -------------------------------------------------------------------------------------------

def test_the_volume_pattern_sets_how_many_rows_may_add_up():
    assert (build_spec(BASE, name="x", label_a="a", label_b="b", pattern=PATTERNS[0]).max_a_per_b,
            build_spec(BASE, name="x", label_a="a", label_b="b", pattern=PATTERNS[0]).max_b_per_a) == (1, 1)
    many_one = build_spec(BASE, name="x", label_a="a", label_b="b", pattern=PATTERNS[1], size_a=4)
    assert (many_one.max_a_per_b, many_one.max_b_per_a) == (4, 1)
    one_many = build_spec(BASE, name="x", label_a="a", label_b="b", pattern=PATTERNS[2], size_b=3)
    assert (one_many.max_a_per_b, one_many.max_b_per_a) == (1, 3)
    both = build_spec(BASE, name="x", label_a="a", label_b="b", pattern=PATTERNS[3], size_a=2, size_b=5)
    assert (both.max_a_per_b, both.max_b_per_a) == (2, 5)


def test_sizes_are_clamped_and_text_is_trimmed():
    spec = build_spec(BASE, name="  Mine  ", label_a="  ", label_b=" Bank ", pattern=PATTERNS[3], size_a=99, size_b="x",
                      match_id_header=" ", match_id_position="nowhere")
    assert spec.name == "Mine" and spec.label_a == "Dataset A" and spec.label_b == "Bank"
    assert spec.max_a_per_b == 6 and spec.max_b_per_a == 2            # clamped to the allowed range
    assert spec.match_id_header == "Match ID" and spec.match_id_position == "last"


def test_a_template_s_columns_survive_the_settings():
    template = ReconSpec(name="T", amount=ColumnPair("AMOUNT", "TOTAL"), align=[ColumnPair("PO", "REF", "digits")],
                         id_a="Num", id_b="No")
    spec = build_spec(template, name="Renamed", label_a="QB", label_b="ERP")
    assert spec.amount == template.amount and spec.align == template.align and (spec.id_a, spec.id_b) == ("Num", "No")
    assert spec.name == "Renamed"


def test_only_a_missing_name_stops_a_template_built_from_settings():
    assert builder_problems(build_spec(BASE, name="", label_a="a", label_b="b")) == ["Give your template a name."]
    assert builder_problems(build_spec(BASE, name="Ok", label_a="a", label_b="b")) == []


# --- the page --------------------------------------------------------------------------------------------------------

def _page(monkeypatch, config=None, fail=None):
    monkeypatch.setattr(ui, "_db_config", lambda: config)
    if fail is not None:
        monkeypatch.setattr(ui, "tenant_connection", fail)
    at = AppTest.from_file("apps/recon/app.py", default_timeout=60)
    at.session_state["recon_mode"] = "Custom mapping"
    at.run()
    assert not at.exception, at.exception
    return at


def _top_level(at):
    return [(c.type, str(getattr(getattr(c, "proto", None), "body", "") or getattr(c, "label", ""))) for _, c in
            sorted(at.main.children.items())]


def test_build_your_template_is_the_first_message_and_the_status_sits_just_below_it(monkeypatch):
    at = _page(monkeypatch)
    order = _top_level(at)
    heading = next(i for i, (_, body) in enumerate(order) if "Build your template" in body)
    status_slot = next(i for i, (kind, _) in enumerate(order) if i > heading and kind == "flex_container")
    assert status_slot == heading + 1                       # the warnings panel is the very next block
    stepper = next(i for i, (_, body) in enumerate(order) if "rec-stepper" in body)
    assert stepper < heading                                # only the stepper comes before the heading
    assert not any("Choose a template to continue" in body for _, body in order)
    assert not any("Choose a template" == body for _, body in order)


def test_next_is_not_greyed_out_and_a_template_can_be_started_without_choosing_one(monkeypatch):
    at = _page(monkeypatch)
    nxt = next(b for b in at.button if b.label == "Next: upload data")
    cont = next(b for b in at.button if b.label == "Continue with this template")
    assert not nxt.disabled and not cont.disabled
    nxt.click().run()
    assert at.session_state["cr_step"] == 1 and at.session_state["cr_spec0"]["name"] == "My mapping"


def test_clearing_the_name_blocks_continuing_and_says_why(monkeypatch):
    at = _page(monkeypatch)
    at.text_input(key="crb_name").set_value("").run()
    assert next(b for b in at.button if b.label == "Next: upload data").disabled
    assert next(b for b in at.button if b.label == "Continue with this template").disabled


def test_the_settings_chosen_here_reach_the_template(monkeypatch):
    at = _page(monkeypatch)
    at.text_input(key="crb_name").set_value("Bank vs Ledger").run()
    at.text_input(key="crb_la").set_value("Bank").run()
    at.text_input(key="crb_lb").set_value("Ledger").run()
    volume = next(g for g in at.get("button_group") if g.key == "crb_pattern")
    volume.set_value(PATTERNS[1]).run()
    next(b for b in at.button if b.label == "Continue with this template").click().run()
    spec = at.session_state["cr_spec0"]
    assert (spec["name"], spec["label_a"], spec["label_b"]) == ("Bank vs Ledger", "Bank", "Ledger")
    assert spec["max_a_per_b"] >= 2 and spec["max_b_per_a"] == 1        # Many : 1 chosen above


def test_an_unreachable_library_is_asked_once_with_a_short_wait_then_left_alone(monkeypatch):
    calls = []

    def unreachable(config, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("connection failed")

    at = _page(monkeypatch, config=object(), fail=unreachable)
    assert calls == [{"connect_timeout": ui.DB_WAIT_SECONDS}]          # a short wait, not the 8-second default
    assert any("could not be reached" in w.value for w in at.warning)
    assert any(b.label == "Try the shared library again" for b in at.button)
    at.run()
    at.run()
    assert len(calls) == 1                                              # the cooldown: no further waits on later clicks
    next(b for b in at.button if b.label == "Try the shared library again").click().run()
    assert len(calls) == 2


def test_a_library_that_never_answers_cannot_hold_the_page_up(monkeypatch):
    import time as _time
    from contextlib import contextmanager

    monkeypatch.setattr(ui, "DB_WAIT_SECONDS", 0.2)

    @contextmanager
    def hangs(config, **kwargs):
        _time.sleep(3)
        yield object()

    started = _time.monotonic()
    at = _page(monkeypatch, config=object(), fail=hangs)
    assert _time.monotonic() - started < 2.5                            # not the 3 seconds the stub sleeps
    assert any("did not answer in time" in w.value for w in at.warning)
    assert any(b.label == "Use template" for b in at.button)            # the example card is still offered
