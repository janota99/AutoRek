"""Saved runs end to end, connecting as the deployed app does (app_login, row-level security on).
Runs only when DB_TEST_ADMIN_URL is set (see conftest.py)."""
import uuid
from datetime import datetime, timezone

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import make_conninfo  # noqa: E402

from apps.recon import run_store  # noqa: E402
from apps.recon.run_log import Actor, build_run_record  # noqa: E402
from db.connection import DatabaseConfig, tenant_connection  # noqa: E402


@pytest.fixture
def config(db_url, org):
    return DatabaseConfig(url=make_conninfo(db_url, user="app_login"), org_id=str(org))


def make_record(ref="REC-1", actor=Actor("pat@example.com", True), control="PASS"):
    return build_run_record(
        metadata={"run_id": ref, "run_timestamp_dt": datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc),
                  "qb_filename": "qb.xlsx", "qb_sha256": "a" * 64, "inf_filename": "inf.xlsx", "inf_sha256": "b" * 64,
                  "fiscal_year": 2026, "fiscal_period": 7},
        metrics={"Control Status": control, "Matched QuickBooks Rows": 5, "Proposed JE Amount": 10.5},
        controls=[{"Check": "Rows tie", "Expected": 5, "Actual": 5, "Difference": 0.0, "Status": "PASS"}],
        mappings={"quickbooks": {"po": "PO"}}, app_version="2.17.0", matching_rule_version="R1", actor=actor)


def test_a_run_round_trips_intact_and_saving_it_twice_writes_once(config):
    record = make_record()
    with tenant_connection(config) as conn:
        first = run_store.save_run(conn, config.org_id, record)
        again = run_store.save_run(conn, config.org_id, record)
    assert first.created and not again.created and first.run_id == again.run_id
    with tenant_connection(config) as conn:
        runs = run_store.list_runs(conn)
    assert len(runs) == 1 and runs[0].intact
    saved = runs[0].record
    assert saved.run_ref == "REC-1" and saved.ran_by == "pat@example.com" and saved.ran_by_verified
    assert saved.headline["Proposed JE Amount"] == 10.5
    assert [(i.role, i.sha256) for i in saved.inputs] == [("infinium", "b" * 64), ("qb", "a" * 64)]


def test_the_database_refuses_to_update_or_delete_history(config):
    with tenant_connection(config) as conn:
        run_id = run_store.save_run(conn, config.org_id, make_record()).run_id
        run_store.add_event(conn, config.org_id, run_id, "note", Actor("pat", True), {"text": "checked"})
    for sql in ("UPDATE recon_runs SET ran_by = 'someone else'", "DELETE FROM recon_runs",
                "UPDATE recon_run_events SET actor = 'x'", "DELETE FROM recon_run_events",
                "DELETE FROM recon_run_inputs", "UPDATE recon_run_inputs SET filename = 'x'"):
        with pytest.raises(psycopg.Error):
            with tenant_connection(config) as conn:
                conn.execute(sql)


def test_events_log_notes_and_reviews_in_order(config):
    with tenant_connection(config) as conn:
        run_id = run_store.save_run(conn, config.org_id, make_record()).run_id
        run_store.add_event(conn, config.org_id, run_id, "note", Actor("pat@example.com", True),
                            {"text": "  looks right "})
        run_store.add_event(conn, config.org_id, run_id, "reviewed", Actor("Unsigned user", False))
    with tenant_connection(config) as conn:
        events = run_store.list_events(conn, run_id)
    assert [(e.event_type, e.actor_verified) for e in events] == [("note", True), ("reviewed", False)]
    assert events[0].detail == {"text": "looks right"}


def test_a_workpaper_is_kept_logged_and_cannot_be_edited_afterwards(config, db_url):
    content = b"PK\x03\x04 fake workbook bytes"
    with tenant_connection(config) as conn:
        run_id = run_store.save_run(conn, config.org_id, make_record()).run_id
        paper_id = run_store.save_workpaper(conn, config.org_id, run_id, kind="primary", filename="w.xlsx",
                                            content=content, actor=Actor("pat", True))
    with tenant_connection(config) as conn:
        info, data = run_store.read_workpaper(conn, paper_id)
        listed = run_store.list_workpapers(conn, run_id)
        events = run_store.list_events(conn, run_id)
    assert data == content and info.size_bytes == len(content) and [p.kind for p in listed] == ["primary"]
    assert [e.event_type for e in events] == ["workpaper_prepared"]
    with psycopg.connect(db_url) as owner:  # even the owner cannot edit it quietly: the trigger forbids it
        with pytest.raises(psycopg.Error):
            owner.execute("UPDATE recon_run_workpapers SET content = %s", (b"x",))


def test_one_organization_never_sees_anothers_runs(config, db_url):
    other = uuid.uuid4()
    with psycopg.connect(db_url) as admin:
        with admin.transaction():
            admin.execute("INSERT INTO organizations (org_id, name) VALUES (%s, 'Other')", (other,))
    with tenant_connection(config) as conn:
        run_store.save_run(conn, config.org_id, make_record("REC-MINE"))
    other_config = DatabaseConfig(url=config.url, org_id=str(other))
    with tenant_connection(other_config) as conn:
        assert run_store.list_runs(conn) == []
    with pytest.raises(psycopg.Error):  # nor write into the first organization
        with tenant_connection(other_config) as conn:
            run_store.save_run(conn, config.org_id, make_record("REC-SNEAK"))


def test_an_unknown_control_verdict_is_rejected_by_the_database(config):
    bad = make_record()
    with pytest.raises(psycopg.Error):
        with tenant_connection(config) as conn:
            conn.execute("INSERT INTO recon_runs (org_id, run_ref, ran_at, ran_by, app_version, matching_rule_version, "
                         "control_status, headline, controls, mapping, record_sha256) VALUES "
                         "(%s, 'X', now(), 'p', '1', '1', 'MAYBE', '{}', '[]', '{}', %s)",
                         (config.org_id, bad.record_sha256))
