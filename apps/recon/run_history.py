"""Streamlit side of saved runs: record a finished run, keep its workpaper, and show the team's run history.

Everything here is optional and safe to ignore: with no database configured nothing is saved and nothing is shown,
and a database failure never stops or changes a reconciliation. It reads results and writes only to the run tables
(``run_store``); it never touches a match, a classification, or a workbook cell.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd
import streamlit as st

from db.connection import DatabaseConfig, DatabaseConfigError, load_config, tenant_connection
from shared.identity import current_identity

from . import run_store
from .matching import APP_VERSION, MATCHING_RULE_VERSION
from .run_log import ROLE_LABELS, Actor, build_run_record, log_to_csv
from .run_status import MONEY_KEYS, SUMMARY_LABELS
from .utils import format_central_timestamp

DB_WAIT_SECONDS = 4          # a history list must never hold the page up
_SAVED_KEY = "recon_saved_runs"      # run_ref -> run_id, for this browser session
_NOTICE_KEY = "recon_run_save_notice"


def _config() -> Optional[DatabaseConfig]:
    try:
        return load_config(secrets=st.secrets)
    except DatabaseConfigError:
        return None


def _actor() -> Actor:
    who = current_identity()
    return Actor(who.name, who.verified)


def _reason(exc: BaseException) -> str:
    if type(exc).__name__ == "UndefinedTable":
        return "run history is not set up in this database yet; an administrator needs to apply migration 004 (py -m db.migrate)"
    return f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}"


def record_run(result: Any, mappings: dict, *, uses_sample: bool = False) -> None:
    """Save the finished run. Never raises; leaves a one-line notice for the page to show.

    A run on built-in sample data is never saved: demo figures must not enter the team's audit trail."""
    config = _config()
    if config is None:
        return
    if uses_sample:
        st.session_state[_NOTICE_KEY] = (result.run_id, "Sample data is never saved to run history.")
        return
    try:
        record = build_run_record(
            metadata=result.metadata, metrics=result.metrics, controls=result.controls.to_dict("records"),
            mappings=mappings, app_version=APP_VERSION, matching_rule_version=MATCHING_RULE_VERSION,
            actor=_actor())
        with tenant_connection(config, connect_timeout=DB_WAIT_SECONDS) as conn:
            saved = run_store.save_run(conn, config.org_id, record)
        st.session_state.setdefault(_SAVED_KEY, {})[record.run_ref] = saved.run_id
        st.session_state[_NOTICE_KEY] = (result.run_id, f"Saved to run history as {record.run_ref}.")
    except Exception as exc:  # noqa: BLE001 - history is a record of the run, never a condition of it
        st.session_state[_NOTICE_KEY] = (
            result.run_id, f"This run was not saved to run history ({_reason(exc)}). The reconciliation is unaffected.")


@st.cache_data(ttl=120, show_spinner=False)
def known_input_runs(hashes: tuple[str, ...]) -> dict[str, str]:
    """Saved runs that already used any of these files (SHA-256 -> run reference); empty with no history. Never raises.

    Cached for two minutes so the page's many reruns do not each wait on the database."""
    config = _config()
    if config is None:
        return {}
    try:
        with tenant_connection(config, connect_timeout=DB_WAIT_SECONDS) as conn:
            return run_store.runs_using_hashes(conn, list(hashes))
    except Exception:  # noqa: BLE001 - a missing history only means no "used before" notes
        return {}


def keep_workpaper(run_ref: str, kind: str, filename: str, content: bytes) -> None:
    """Keep a workpaper that was just built, if its run was saved. Never raises."""
    config, run_id = _config(), st.session_state.get(_SAVED_KEY, {}).get(run_ref)
    if config is None or run_id is None:
        return
    try:
        with tenant_connection(config, connect_timeout=DB_WAIT_SECONDS) as conn:
            run_store.save_workpaper(conn, config.org_id, run_id, kind=kind, filename=filename, content=content,
                                     actor=_actor())
    except Exception as exc:  # noqa: BLE001
        st.caption(f"The workpaper was not kept in run history ({_reason(exc)}). Your download is unaffected.")


def render_save_notice(run_ref: str) -> None:
    notice = st.session_state.get(_NOTICE_KEY)
    if notice and notice[0] == run_ref:
        st.caption(notice[1])


def _central(value: Any) -> str:
    return format_central_timestamp(value)


def _money(value: Any) -> str:
    return "" if value is None else f"${float(value):,.2f}"


def _table(runs: list) -> pd.DataFrame:
    rows = []
    for saved in runs:
        r = saved.record
        row = {
            "Run": r.run_ref, "Ran at": _central(r.ran_at),
            "Ran by": r.ran_by if r.ran_by_verified else f"{r.ran_by} (unverified)",
            "Period": "" if r.fiscal_period is None else f"FY{r.fiscal_year} P{r.fiscal_period:02d}",
            "Controls": r.control_status, "Posting": r.posting_status or "",
        }
        for key, label in SUMMARY_LABELS.items():
            if key in ("Control Status",):
                continue
            value = r.headline.get(key)
            row[label] = _money(value) if key in MONEY_KEYS else ("" if value is None else f"{int(value):,}")
        row["Record"] = "intact" if saved.intact else "ALTERED"
        rows.append(row)
    return pd.DataFrame(rows)


def render_history_panel() -> None:
    """The run history for the organization. Draws nothing when no database is configured."""
    config = _config()
    if config is None:
        return
    with st.expander("Run history", expanded=False):
        try:
            with tenant_connection(config, connect_timeout=DB_WAIT_SECONDS) as conn:
                runs = run_store.list_runs(conn, limit=100)
        except Exception as exc:  # noqa: BLE001
            st.caption(f"Run history could not be read right now ({_reason(exc)}).")
            return
        if not runs:
            st.caption("No runs saved yet. Each time you run a reconciliation it is saved here with who ran it, the "
                       "file fingerprints, the controls, and the rule versions.")
            return
        if any(not r.intact for r in runs):
            st.error("At least one saved run no longer matches its recorded digest. It was changed after it was saved; "
                     "treat it as unreliable and tell your administrator.")
        st.dataframe(_table(runs), width="stretch", hide_index=True)
        st.download_button("Download run log (CSV)", data=log_to_csv(r.record for r in runs),
                           file_name="Recon_run_log.csv", mime="text/csv")
        _render_run_detail(config, runs)


def _render_run_detail(config: DatabaseConfig, runs: list) -> None:
    by_ref = {r.record.run_ref: r for r in runs}
    ref = st.selectbox("Open a run", list(by_ref), key="recon_history_pick")
    saved = by_ref[ref]
    st.markdown("**Files used** (fingerprints only; the files themselves are not stored)")
    st.dataframe(pd.DataFrame([{"File": ROLE_LABELS.get(i.role, i.role), "Name": i.filename,
                                "Worksheet": i.sheet_name or "", "SHA-256": i.sha256} for i in saved.record.inputs]),
                 width="stretch", hide_index=True)
    st.caption(f"Engine {saved.record.app_version}, matching rules {saved.record.matching_rule_version}. "
               "Re-uploading the same files with the same settings and versions reproduces this run.")
    try:
        with tenant_connection(config, connect_timeout=DB_WAIT_SECONDS) as conn:
            events = run_store.list_events(conn, saved.run_id)
            papers = run_store.list_workpapers(conn, saved.run_id)
    except Exception as exc:  # noqa: BLE001
        st.caption(f"The log for this run could not be read right now ({_reason(exc)}).")
        return
    if events:
        st.markdown("**Log**")
        st.dataframe(pd.DataFrame([{
            "When": _central(e.created_at), "Who": e.actor if e.actor_verified else f"{e.actor} (unverified)",
            "What": e.event_type.replace("_", " "), "Detail": e.detail.get("text") or e.detail.get("filename", ""),
        } for e in events]), width="stretch", hide_index=True)
    for paper in papers:
        try:
            with tenant_connection(config, connect_timeout=DB_WAIT_SECONDS) as conn:
                found = run_store.read_workpaper(conn, paper.workpaper_id)
        except Exception as exc:  # noqa: BLE001
            st.caption(f"{paper.filename} could not be read ({_reason(exc)}).")
            continue
        if found:
            st.download_button(f"Download {paper.filename} (saved {_central(paper.created_at)})", data=found[1],
                               file_name=paper.filename, key=f"hist_dl_{paper.workpaper_id}",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    note = st.text_area("Add a note to this run", max_chars=run_store.MAX_NOTE_CHARS, key="recon_history_note")
    left, right = st.columns(2)
    actor = _actor()
    if not actor.verified:
        st.caption("No real sign-in is active, so notes and reviews are logged as unverified.")
    with left:
        add = st.button("Add note", disabled=not note.strip(), key="recon_history_add_note")
    with right:
        mark = st.button("Mark reviewed", key="recon_history_mark_reviewed",
                         help="Records that you looked at this run. It changes no match or classification.")
    if add or mark:
        try:
            with tenant_connection(config, connect_timeout=DB_WAIT_SECONDS) as conn:
                run_store.add_event(conn, config.org_id, saved.run_id, "note" if add else "reviewed", actor,
                                    {"text": note} if add else {})
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not add that to the log ({_reason(exc)}).")
