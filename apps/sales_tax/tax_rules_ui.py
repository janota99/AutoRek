"""The Texas Tax Rules screen: search and filters over 16 principles and their 100 worked scenarios.

Presentation only. Everything shown comes from ``tax_rules`` (the ``taxRules`` documents); this module formats it.
Styles are the ``tr-*`` classes in shared/styles/pages/sales-tax.css. The rules are read from the Atlas collection
when a database is configured and reachable, and from the copy bundled in ``mongodb/taxRules.json`` otherwise.
"""
from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

from . import contractor_guide, tax_rules
from .tax_rules import Filters

_GUIDE_JS = (Path(__file__).resolve().parent / "contractor_guide.js").read_text(encoding="utf-8")

_E = html.escape
_STATE = "tr_"          # session-state prefix for this tab's widgets
_ENTRIES = _STATE + "custom_entries"      # the visitor's own rules (their session only)
_IMPORTED = _STATE + "imported_file"
_TREATMENT_TONE = {"Generally taxable": "tax", "Generally nontaxable": "free", "Depends on the facts": "depends"}


@st.cache_data(ttl=300, show_spinner=False)
def _rules() -> tuple[list[dict], str]:
    """The rules and where they came from. Cached for five minutes so a search never waits on the network."""
    def fetch():
        from mongodb.connection import connect, load_config  # absolute: ``mongodb`` is a top-level folder

        try:
            secrets = st.secrets
            secrets.get("mongodb")  # touching it surfaces a missing secrets file here, not later
        except Exception:  # noqa: BLE001
            secrets = None
        config = load_config(secrets=secrets)
        if config is None:
            return []
        client = connect(config, timeout_ms=3000)
        return [{**d} for d in client[config.database]["taxRules"].find({})]

    return tax_rules.load_rules(fetch)


def _scenario_html(s: dict) -> str:
    industry = "" if s["industry"] == tax_rules.ANY_BUSINESS else f'<span class="tr-tag">{_E(s["industry"])}</span>'
    return (
        '<div class="tr-scn">'
        f'<div class="tr-scn-head"><span class="tr-no">#{s["no"]}</span><h4>{_E(s["title"])}</h4>{industry}</div>'
        '<div class="tr-when">'
        f'<div class="tr-tax"><b>Taxable when</b><p>{_E(s["taxableWhen"])}</p></div>'
        f'<div class="tr-free"><b>Nontaxable or exempt when</b><p>{_E(s["nontaxableWhen"])}</p></div></div>'
        f'<p class="tr-practice"><b>In practice:</b> {_E(s["inPractice"])}</p>'
        f'<p class="tr-cite">{_E(s["authority"])}</p>'
        '</div>'
    )


def _sources_html(doc: dict) -> str:
    links = []
    for src in doc["sources"]:
        url = tax_rules.safe_url(src.get("url"))
        label = f'{_E(src["key"])}: {_E(src["label"])}'
        links.append(f'<a href="{_E(url, quote=True)}" target="_blank" rel="noopener noreferrer">{label}</a>' if url
                     else f"<span>{label}</span>")
    return '<p class="tr-sources"><b>Sources</b> ' + " &middot; ".join(links) + "</p>"


def _status_html(doc: dict) -> str:
    if doc.get("custom"):
        return '<span class="tr-status tr-custom">Your rule, not reviewed</span>'
    if doc.get("status") == "verified" and doc.get("lastVerified"):
        return f'<span class="tr-status tr-verified">Verified {_E(str(doc["lastVerified"])[:10])}</span>'
    return '<span class="tr-status tr-drafted">Drafted, not yet verified</span>'


def _remove_entry(index: int) -> None:
    entries = list(st.session_state.get(_ENTRIES, []))
    if 0 <= index < len(entries):
        entries.pop(index)
    st.session_state[_ENTRIES] = entries


def _principle(doc: dict, *, expanded: bool) -> None:
    shown, total = len(doc["scenarios"]), doc.get("totalScenarios", len(doc["scenarios"]))
    count = f"{shown} of {total} scenarios" if shown != total else f"{total} scenarios"
    mine = "Your rule  ·  " if doc.get("custom") else ""
    with st.expander(f"{mine}{doc['title']}  ·  {doc['usualTreatment']}  ·  {count}", expanded=expanded):
        tone = _TREATMENT_TONE.get(doc["usualTreatment"], "depends")
        cites = "".join(f"<li>{_E(c)}</li>" for c in doc["authorities"])
        st.html(
            f'<div class="tr-head"><span class="tr-treat tr-{tone}-badge">{_E(doc["usualTreatment"])}</span>'
            f'{_status_html(doc)}<span class="tr-cat">{_E(doc["category"])}</span></div>'
            f'<p class="tr-summary">{_E(doc["summary"])}</p>'
            f'<ul class="tr-cites">{cites}</ul>{_sources_html(doc)}'
            + "".join(_scenario_html(s) for s in doc["scenarios"])
        )
        if doc.get("custom"):
            st.button("Remove this rule", key=f"tr-rm-{doc['customIndex']}", icon=":material/delete:",
                      on_click=_remove_entry, args=(doc["customIndex"],))


def _contractor_guide(docs: list[dict]) -> None:
    """The click-through decision guide for contractor bills. Drawn by contractor_guide.js; the nested outline is
    the same tree for anyone reading without the script."""
    st.html(
        '<div class="cg-root" data-depth="0">'
        '<div class="cg-top"><div><h3 class="cg-title">Should we pay Texas sales tax on this contractor bill?</h3>'
        '<p class="cg-sub">Answer two or three questions to see the usual result and the scenarios behind it.</p></div>'
        '<button type="button" class="cg-btn cg-reset">Start over</button></div>'
        '<div class="cg-trail" aria-label="Your answers so far"></div>'
        '<div class="cg-stage"></div>'
        '<div class="cg-live" role="status" aria-live="polite"></div>'
        '<details class="cg-outline-wrap"><summary>See the whole guide as a list</summary>'
        f"{contractor_guide.outline_html()}</details></div>"
        f"<script>window.PP_CG_TREE = {contractor_guide.tree_json(docs)};\n{_GUIDE_JS}</script>",
        unsafe_allow_javascript=True,
    )


def _custom_section() -> None:
    """Add, download, load and (per rule, in the results) remove the visitor's own rules."""
    entries = list(st.session_state.get(_ENTRIES, []))
    label = "Add your own rule" + (f" ({len(entries)} added)" if entries else "")
    with st.expander(label, expanded=False):
        st.caption(
            "Your own rules sit beside the reference below, marked as yours. They stay in this browser session only "
            "and are never added to the shared reference: download them to keep them. Do not enter confidential "
            "information."
        )
        with st.form("tr_add_rule", clear_on_submit=True):
            title = st.text_input("Rule title", max_chars=120, placeholder="e.g. Equipment rentals to a customer")
            summary = st.text_area("What the rule says", max_chars=800, height=90,
                                   placeholder="Plain-language statement of the rule")
            c1, c2 = st.columns(2)
            category = c1.selectbox("Category", tax_rules.CATEGORY_CHOICES)
            treatment = c2.selectbox("Usual treatment", tax_rules.TREATMENT_CHOICES, index=2)
            t1, t2 = st.columns(2)
            taxable = t1.text_area("Taxable when (optional)", max_chars=600, height=80)
            free = t2.text_area("Nontaxable or exempt when (optional)", max_chars=600, height=80)
            practice = st.text_area("In practice (optional)", max_chars=600, height=70)
            a1, a2 = st.columns(2)
            authority = a1.text_input("Authority or citation (optional)", max_chars=200,
                                      placeholder="e.g. Tax Code section, rule, or ruling")
            url = a2.text_input("Source link (optional, https only)", max_chars=300, placeholder="https://")
            submitted = st.form_submit_button("Add rule", type="primary")
        if submitted:
            if len(entries) >= tax_rules.MAX_CUSTOM:
                st.error(f"You can keep {tax_rules.MAX_CUSTOM} of your own rules in a session. Remove one first.")
            else:
                try:
                    entry = tax_rules.clean_custom_entry({
                        "title": title, "category": category, "treatment": treatment, "summary": summary,
                        "taxableWhen": taxable, "nontaxableWhen": free, "inPractice": practice,
                        "authority": authority, "url": url})
                except ValueError as exc:
                    st.error(f"Not added: {exc}")
                else:
                    st.session_state[_ENTRIES] = entries + [entry]
                    st.toast(f"Added \u201c{entry['title']}\u201d. Search for it below, or filter by category.")
                    st.rerun()
        if entries:
            st.download_button("Download my rules (.json)", tax_rules.export_entries(entries),
                               file_name="my-tax-rules.json", mime="application/json", icon=":material/download:",
                               key="tr-export")
        upload = st.file_uploader("Load rules you downloaded earlier", type=["json"], key="tr-import")
        if upload is not None and st.session_state.get(_IMPORTED) != upload.file_id:
            st.session_state[_IMPORTED] = upload.file_id
            try:
                loaded = tax_rules.import_entries(upload.getvalue().decode("utf-8", errors="replace"), len(entries))
            except ValueError as exc:
                st.error(f"Not loaded: {exc}")
            else:
                st.session_state[_ENTRIES] = entries + loaded
                st.toast(f"Loaded {len(loaded)} rule(s).")
                st.rerun()


def render_tax_rules() -> None:
    reference, origin = _rules()
    st.html(
        '<div class="st-section"><h2>Texas Tax Rules</h2>'
        '<p>Sixteen principles and the worked scenarios under them, with the authority for each. Search, or narrow by '
        'category, usual treatment, or provision.</p></div>'
    )
    st.html(
        '<div class="tr-note" role="note"><b>Reference only, not tax advice.</b> Entries are drafted from Texas '
        'Comptroller and statute sources and are not yet verified by a person; each one shows its status and links to '
        'its sources. Confirm against the source, and the facts of the transaction, before relying on an entry.</div>'
    )

    with st.container(key="tr-guide"):
        _contractor_guide(reference)
    _custom_section()
    mine = tax_rules.custom_docs(list(st.session_state.get(_ENTRIES, [])))
    docs = reference + mine

    query = st.text_input("Search the rules", key=_STATE + "query", placeholder="Try: ripening, forklift, delivery charge, janitorial")
    c1, c2, c3 = st.columns(3, gap="medium")
    cats = c1.multiselect("Category", tax_rules.categories(docs), key=_STATE + "cats")
    treats = c2.multiselect("Usual treatment", tax_rules.treatments(docs), key=_STATE + "treats")
    provs = c3.multiselect("Provision", tax_rules.all_provisions(docs), key=_STATE + "provs")
    scope = st.segmented_control(
        "Scenarios", ["All scenarios", "Any business only"], default="All scenarios", key=_STATE + "scope",
        help="Some scenarios are specific to food handling (ripening, cafes, deli). Any business only hides them.",
    )

    filters = Filters(query=query or "", categories=tuple(cats), treatments=tuple(treats), provisions=tuple(provs),
                      any_business_only=(scope == "Any business only"))
    results = tax_rules.search(docs, filters)
    principles, scenarios = tax_rules.totals(results)
    all_principles, all_scenarios = tax_rules.totals(tax_rules.search(docs))

    where = "the database" if origin == "database" else "the copy bundled with the app"
    yours = f" ({len(mine)} of them yours)" if mine else ""
    if filters.active:
        st.caption(f"Showing {principles} of {all_principles} principles and {scenarios} of {all_scenarios} scenarios"
                   f"{yours}. Reference read from {where}.")
    else:
        st.caption(f"{all_principles} principles, {all_scenarios} scenarios{yours}. Reference read from {where}.")

    if not results:
        st.info("No rule matches those filters. Clear a filter or try fewer words; every word in the search must appear.")
        return
    # With a narrow result the answers are shown open; the full list starts collapsed so it can be scanned.
    open_all = filters.active and principles <= 4
    for doc in results:
        _principle(doc, expanded=open_all)
