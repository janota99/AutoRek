"""The front landing page: its example numbers tie out, it renders, and the purchase sequence runs end to end."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from apps import landing_data as data

ROOT = Path(__file__).resolve().parents[2]
LANDING = str(ROOT / "apps" / "landing.py")


# --- the example numbers ----------------------------------------------------------------------------------------------

def test_reconciliation_example_accounts_for_every_row():
    r = data.reconcile()
    qb_ids = {q for q, *_ in r["matched"]} | {e[0] for e in r["exceptions"] if e[0]}
    inf_ids = {i for _, i, *_ in r["matched"]} | {e[1] for e in r["exceptions"] if e[1]}
    assert qb_ids == {row[0] for row in data.QB_ROWS}
    assert inf_ids == {row[0] for row in data.INF_ROWS}
    assert (len(r["matched"]), len(r["exceptions"])) == (6, 3)


def test_a_near_miss_is_never_matched():
    """Exact-cent rule: the $27.00 difference stays an open item, it is not matched as the closest candidate."""
    r = data.reconcile()
    assert all(m[3] != "48270" for m in r["matched"])
    assert any("differs by $27.00" in e[5] for e in r["exceptions"])


def test_fifo_example_ties_out_and_uses_oldest_layers_first():
    f = data.fifo()
    assert f["cost_of_usage"] + f["ending_value"] == f["available_value"]
    assert [u[0] for u in f["usage"]] == ["Opening (P11 ending)", "Receipt R-1"]
    assert f["usage"][0][1] == 4000                       # the opening layer is used up before R-1 is touched
    assert f["ending_units"] == sum(layer[1] for layer in data.LAYERS) - data.UNITS_USED
    assert (f["cost_of_usage"], f["ending_value"]) == (138500, 147500)


def test_transaction_review_control_is_zero():
    v = data.review()
    assert v["control_diff"] == 0
    assert sum(len(q) for q in v["queues"].values()) == v["rows"]


def test_sample_downloads_are_csv_with_the_rows_shown_on_the_page():
    exceptions = data.report_csv("exceptions").decode().splitlines()
    assert exceptions[0] == "QuickBooks Row,Infinium Row,Customer,PO,Amount,Why it is open"
    assert len(exceptions) == 1 + 3
    assert any("5425.10" in line for line in exceptions)
    assert len(data.report_csv("transactions").decode().splitlines()) == 1 + len(data.TXN_ROWS)
    with pytest.raises(KeyError):
        data.report_csv("nope")


# --- the page ---------------------------------------------------------------------------------------------------------------

def _run() -> AppTest:
    at = AppTest.from_file(LANDING, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def test_landing_page_shows_every_section_and_the_three_purchase_buttons():
    at = _run()
    labels = {b.label for b in at.button}
    assert {"Preview Report", "Choose Starter", "Choose Professional", "Contact Sales"} <= labels
    assert len(at.get("download_button")) == 3


def test_purchase_sequence_runs_from_plan_to_onboarding():
    at = _run()
    _button(at, "Choose Professional").click().run()
    assert not at.exception and at.session_state["pp_l_step"] == 2           # review and total
    _button(at, "Continue to account").click().run()
    assert at.session_state["pp_l_step"] == 3                                 # account

    at.text_input[0].set_value("Test Accountant")
    at.text_input[1].set_value("test@example.com")
    _button(at, "Create account").click().run()
    assert at.session_state["pp_l_step"] == 4                                 # simulated checkout
    assert at.session_state["pp_l_account"]["email"] == "test@example.com"

    # A bad card is refused and nothing is recorded.
    fields = {t.label: t for t in at.text_input}
    fields["Name on card"].set_value("Test Accountant")
    fields["Card number"].set_value("4242 4242 4242 4241")
    fields["Expiration (MM/YY)"].set_value("12/99")
    fields["Security code (CVC)"].set_value("123")
    fields["Billing ZIP"].set_value("60601")
    _button(at, "Place order (demo)").click().run()
    assert at.session_state["pp_l_step"] == 4 and not "pp_register" in at.session_state

    fields = {t.label: t for t in at.text_input}
    fields["Name on card"].set_value("Test Accountant")
    fields["Card number"].set_value("4242 4242 4242 4242")
    fields["Expiration (MM/YY)"].set_value("12/99")
    fields["Security code (CVC)"].set_value("123")
    fields["Billing ZIP"].set_value("60601")
    _button(at, "Place order (demo)").click().run()
    assert at.session_state["pp_l_step"] == 5                                 # confirmation
    entry = at.session_state["pp_register"][-1]
    assert entry["status"] == "Demo: not charged" and entry["total"] == 10825
    assert entry["method"] == "Visa ending 4242"                              # brand and last four only
    assert "4242 4242" not in str(at.session_state["pp_register"])


def test_onboarding_needs_every_field_mapped_and_offers_the_workspace():
    # A fresh AppTest: it cannot rerun after the in-form rerun above, which leaves the submitted form in its tree.
    at = AppTest.from_file(LANDING, default_timeout=60)
    at.session_state["pp_l_plan"] = "professional"
    at.session_state["pp_l_account"] = {"name": "Test Accountant", "email": "test@example.com"}
    at.session_state["pp_l_step"] = 5
    at.session_state["pp_register"] = [{"cycle": "Monthly", "total": 10825}]
    at.run()
    assert not at.exception, at.exception
    _button(at, "Set Up Your First Workflow").click().run()
    assert at.session_state["pp_l_step"] == 6
    assert not _button(at, "Enter the workspace").disabled
    at.selectbox[0].select("(not mapped)").run()
    assert _button(at, "Enter the workspace").disabled


def test_sign_in_needs_an_account_created_in_this_session():
    at = _run()
    _button(at, "Choose Starter").click().run()
    _button(at, "Continue to account").click().run()
    sign_in = next(t for t in at.text_input if t.label == "Email")
    sign_in.set_value("nobody@example.com")
    _button(at, "Sign in").click().run()
    assert at.session_state["pp_l_step"] == 3 and any("No demo account" in e.value for e in at.error)


def test_enterprise_is_a_contact_form_not_a_purchase():
    at = _run()
    _button(at, "Contact Sales").click().run()
    assert not at.exception
    assert "pp_l_plan" not in at.session_state


def test_enter_the_workspace_opens_the_chosen_tool_with_its_sample_data():
    # Through app.py, so the pages are registered the way st.switch_page needs them.
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90)
    at.session_state["pp_l_plan"] = "professional"
    at.session_state["pp_l_account"] = {"name": "Test Accountant", "email": "test@example.com"}
    at.session_state["pp_l_step"] = 6
    at.run()
    assert not at.exception, at.exception
    _button(at, "Enter the workspace").click().run()
    assert not at.exception, at.exception
    assert dict(at.query_params) == {"demo": ["1"]}


# --- one product language across the landing page, pricing, and workspace ------------------------------------------------

def test_solution_names_match_the_registry_pricing_and_the_demo_banner():
    from shared.layout import APPS
    registry = {a.url_path: a for a in APPS}
    for solution in data.SOLUTIONS:
        app = registry[solution.app_path]
        assert app.title == solution.name                      # nav bar, Dashboard card, pricing, landing: one name
        assert app.icon == f":material/{solution.icon}:"       # and one icon


def test_pricing_never_lists_a_planned_or_prototype_capability_as_included():
    from apps.sales_page import TIERS, _feature_li
    risky = ("session history", "api integrations", "audit logs", "multi-user", "invoice lifecycle hub")
    for tier in TIERS:
        for feature in tier.features:
            if any(word in feature.lower() for word in risky):
                assert feature.startswith(("Planned: ", "Prototype: ")), feature
                assert 'class="pp-tag"' in _feature_li(feature)


def test_landing_has_one_header_and_no_second_logo():
    at = _run()
    page = "".join(h.proto.body for h in at.get("html"))
    assert 'class="pp-l-nav"' in page and 'class="pp-l-logo"' not in page
    assert "settle" not in page.lower()


def _banner_app():
    import streamlit as st
    from apps import demo_banner
    demo_banner.render(st.session_state["_path"])


def _banner(path, demo):
    at = AppTest.from_function(_banner_app, default_timeout=30)
    at.session_state["_path"] = path
    if demo:
        at.query_params["demo"] = "1"
    at.run()
    assert not at.exception, at.exception
    return at


def test_demo_banner_names_the_solution_and_offers_a_way_back():
    at = _banner("recon", demo=True)
    html_blocks = " ".join(h.proto.body for h in at.get("html"))
    assert "Data Reconciliation Studio" in html_blocks and "QuickBooks and Infinium" in html_blocks
    assert any(b.label == "Back to solutions" for b in at.button)


def test_demo_banner_stays_away_without_demo_mode_or_for_other_pages():
    assert not _banner("recon", demo=False).get("html")
    assert not _banner("invoice-hub", demo=True).get("html")
