"""Runs the migrations against a real PostgreSQL and checks that the database enforces what the app relies on.

Skipped unless DB_TEST_ADMIN_URL points at a server where the test may create (and drop) its own throwaway database,
for example  postgresql://postgres@127.0.0.1:55432/postgres . Needs `pip install "psycopg[binary]"`.
"""

import os
import uuid
from decimal import Decimal

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import make_conninfo  # noqa: E402


pytestmark = pytest.mark.skipif(not os.environ.get("DB_TEST_ADMIN_URL"), reason="set DB_TEST_ADMIN_URL to run the live database tests")


def layer(alias, seq, qty, value):
    return (alias, seq, Decimal(qty), Decimal(value))


def close_period(conn, org_id, year, period, beginning=(), ending=(), ending_qty=None, ending_value=None):
    """Insert one closure with its layers in a single transaction (the deferred checks run on exit)."""
    qty = sum(l[2] for l in ending) if ending_qty is None else Decimal(ending_qty)
    value = sum(l[3] for l in ending) if ending_value is None else Decimal(ending_value)
    with conn.transaction():
        closure_id = conn.execute(
            "INSERT INTO fifo_period_closures (org_id, fiscal_year, period, engine_version, snapshot_schema_ver, "
            "run_signature, control_counts, ending_qty, ending_value, closed_by) "
            "VALUES (%s, %s, %s, '2.2.0', 2, 'sig', '{}', %s, %s, 'tester') RETURNING closure_id",
            (org_id, year, period, qty, value)).fetchone()[0]
        for kind, rows in (("beginning", beginning), ("ending", ending)):
            for alias, seq, q, v in rows:
                conn.execute(
                    "INSERT INTO fifo_layers (org_id, closure_id, alias, kind, layer_seq, received, qty, unit_cost, total_value) "
                    "VALUES (%s, %s, %s, %s, %s, '2026-06-01', %s, 0.5, %s)",
                    (org_id, closure_id, alias, kind, seq, q, v))
    return closure_id


ENDING = (layer(1, 1, "100", "50.00"), layer(1, 2, "40", "20.00"), layer(2, 1, "10", "5.00"))


def test_sequence_gap_duplicate_and_year_rollover(conn, org):
    close_period(conn, org, 2026, 12, ending=ENDING)  # the opening seed may be any period
    with pytest.raises(psycopg.Error, match="in sequence"):
        close_period(conn, org, 2027, 1, beginning=ENDING, ending=ENDING)
    close_period(conn, org, 2026, 13, beginning=ENDING, ending=ENDING)
    with pytest.raises(psycopg.Error):
        close_period(conn, org, 2026, 13, beginning=ENDING, ending=ENDING)
    close_period(conn, org, 2027, 1, beginning=ENDING, ending=ENDING)  # P13 rolls into next year's P1


def test_beginning_layers_must_equal_prior_ending(conn, org):
    close_period(conn, org, 2026, 12, ending=ENDING)
    wrong = (layer(1, 1, "100", "50.00"), layer(1, 2, "40", "20.01"), layer(2, 1, "10", "5.00"))
    with pytest.raises(psycopg.Error, match="Rollforward"):
        close_period(conn, org, 2026, 13, beginning=wrong, ending=ENDING)
    with pytest.raises(psycopg.Error, match="Rollforward"):
        close_period(conn, org, 2026, 13, beginning=ENDING[:2], ending=ENDING)  # a layer missing
    close_period(conn, org, 2026, 13, beginning=ENDING, ending=ENDING)


def test_ending_totals_accept_the_engine_tolerance_but_no_more(conn, org):
    close_period(conn, org, 2026, 12, ending=ENDING, ending_qty="150.0001", ending_value="75.01")  # within 0.0001 / 0.01
    with pytest.raises(psycopg.Error, match="exceeds tolerance"):
        close_period(conn, org, 2026, 13, beginning=ENDING, ending=ENDING, ending_value="75.02")
    with pytest.raises(psycopg.Error, match="exceeds tolerance"):
        close_period(conn, org, 2026, 13, beginning=ENDING, ending=ENDING, ending_qty="150.001")


def test_negative_layers_are_rejected(conn, org):
    with pytest.raises(psycopg.Error):
        close_period(conn, org, 2026, 12, ending=(layer(1, 1, "-1", "0"),))


def test_only_the_latest_closed_period_can_be_reopened(conn, org):
    first = close_period(conn, org, 2026, 12, ending=ENDING)
    second = close_period(conn, org, 2026, 13, beginning=ENDING, ending=ENDING)
    reopen = "UPDATE fifo_period_closures SET status = 'reopened', reopened_by = 'tester', reopened_at = now() WHERE closure_id = %s"
    with pytest.raises(psycopg.Error, match="latest closed"):
        with conn.transaction():
            conn.execute(reopen, (first,))
    with conn.transaction():
        conn.execute(reopen, (second,))
    with conn.transaction():
        conn.execute(reopen, (first,))  # now the latest closed one
    # a reopened period can be closed again, and the sequence continues from the latest closed one
    close_period(conn, org, 2026, 12, ending=ENDING)


def test_closures_cannot_be_edited_or_deleted(conn, org):
    closure = close_period(conn, org, 2026, 12, ending=ENDING)
    for sql in ("UPDATE fifo_period_closures SET ending_value = 1 WHERE closure_id = %s",
                "UPDATE fifo_period_closures SET status = 'reopened', reopened_at = now() WHERE closure_id = %s",
                "UPDATE fifo_period_closures SET status = 'reopened', reopened_by = 'x', reopened_at = now(), closed_by = 'y' WHERE closure_id = %s",
                "DELETE FROM fifo_period_closures WHERE closure_id = %s",
                "UPDATE fifo_layers SET qty = 1 WHERE closure_id = %s",
                "DELETE FROM fifo_layers WHERE closure_id = %s"):
        with pytest.raises(psycopg.Error):
            with conn.transaction():
                conn.execute(sql, (closure,))


def test_current_layers_follow_the_latest_closed_period(conn, org):
    close_period(conn, org, 2026, 12, ending=ENDING)
    newer = (layer(1, 1, "90", "45.00"),)
    second = close_period(conn, org, 2026, 13, beginning=ENDING, ending=newer)
    with conn.transaction():
        conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org),))
        conn.execute("SET LOCAL ROLE app_runtime_role")
        rows = conn.execute("SELECT closure_id, qty FROM fifo_current_layers").fetchall()
    assert rows == [(second, Decimal("90.0000"))]


def test_organizations_cannot_be_deleted(conn, org):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with conn.transaction():
            conn.execute("DELETE FROM organizations WHERE org_id = %s", (org,))


def test_cross_tenant_references_are_refused(conn, org):
    other = uuid.uuid4()
    with conn.transaction():
        conn.execute("INSERT INTO organizations (org_id, name) VALUES (%s, 'Other Co')", (other,))
        conn.execute("INSERT INTO fifo_products (org_id, alias, name) VALUES (%s, 1, 'Caps')", (other,))
    closure = close_period(conn, org, 2026, 12, ending=ENDING)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with conn.transaction():
            conn.execute(
                "INSERT INTO fifo_layers (org_id, closure_id, alias, kind, layer_seq, received, qty, unit_cost, total_value) "
                "VALUES (%s, %s, 1, 'ending', 9, 'x', 1, 1, 1)", (other, closure))


def test_row_level_security_isolates_tenants(conn, org):
    other = uuid.uuid4()
    with conn.transaction():
        conn.execute("INSERT INTO organizations (org_id, name) VALUES (%s, 'Other Co')", (other,))
        conn.execute("INSERT INTO fifo_products (org_id, alias, name) VALUES (%s, 1, 'Caps')", (other,))
    close_period(conn, org, 2026, 12, ending=ENDING)
    close_period(conn, other, 2026, 12, ending=(layer(1, 1, "7", "3.50"),))

    def visible(org_setting):
        with conn.transaction():
            if org_setting is not None:
                conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org_setting),))
            conn.execute("SET LOCAL ROLE app_runtime_role")
            return conn.execute("SELECT count(*), count(DISTINCT org_id) FROM fifo_layers").fetchone()

    assert visible(org) == (3, 1)
    assert visible(other) == (1, 1)
    assert visible(None) == (0, 0)  # no tenant set, no rows
    with pytest.raises(psycopg.Error):  # cannot write another tenant's rows
        with conn.transaction():
            conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org),))
            conn.execute("SET LOCAL ROLE app_runtime_role")
            conn.execute("INSERT INTO fifo_products (org_id, alias, name) VALUES (%s, 5, 'Sneaky')", (other,))


def test_runtime_role_cannot_delete_or_rewrite_history(conn, org):
    closure = close_period(conn, org, 2026, 12, ending=ENDING)
    for sql in ("DELETE FROM fifo_layers WHERE closure_id = %s", "UPDATE fifo_layers SET qty = 0 WHERE closure_id = %s",
                "DELETE FROM fifo_period_closures WHERE closure_id = %s"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org),))
                conn.execute("SET LOCAL ROLE app_runtime_role")
                conn.execute(sql, (closure,))


def _spec_row(org_id, template_id, version, label_b="Infinium"):
    return ("INSERT INTO recon_mapping_versions (template_id, org_id, version, spec, spec_version, spec_sha256, label_a, "
            "label_b, max_a_per_b, max_b_per_a, align_count, created_by) VALUES (%s, %s, %s, '{}', 1, 'h', 'QuickBooks', "
            "%s, 2, 1, 1, 'tester')", (template_id, org_id, version, label_b))


def test_mapping_versions_are_immutable_and_the_latest_view_works(conn, org):
    template = uuid.uuid4()
    with conn.transaction():
        conn.execute("INSERT INTO recon_mapping_templates (template_id, org_id, name, created_by) VALUES (%s, %s, 'QB to Inf', 't')",
                     (template, org))
        conn.execute(*_spec_row(org, template, 1))
        conn.execute(*_spec_row(org, template, 2))
    with pytest.raises(psycopg.Error):  # same version twice
        with conn.transaction():
            conn.execute(*_spec_row(org, template, 2))
    with pytest.raises(psycopg.Error):  # the two datasets must have different names
        with conn.transaction():
            conn.execute(*_spec_row(org, template, 3, label_b="QuickBooks"))
    with pytest.raises(psycopg.Error, match="append-only"):
        with conn.transaction():
            conn.execute("UPDATE recon_mapping_versions SET change_note = 'x' WHERE template_id = %s", (template,))
    with conn.transaction():
        conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org),))
        conn.execute("SET LOCAL ROLE app_runtime_role")
        assert conn.execute("SELECT version FROM recon_mapping_latest").fetchall() == [(2,)]
        conn.execute("UPDATE recon_mapping_templates SET archived_at = now() WHERE template_id = %s", (template,))
        assert conn.execute("SELECT count(*) FROM recon_mapping_latest").fetchone() == (0,)  # archived hides it


def test_supabase_web_roles_have_no_access_to_anything(conn):
    rows = conn.execute("SELECT c.relname, r.rolname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, "
                        "(VALUES ('anon'), ('authenticated')) AS r(rolname) "
                        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'S') "
                        "AND (has_table_privilege(r.rolname, c.oid, 'SELECT,INSERT,UPDATE,DELETE') "
                        "     OR (c.relkind = 'S' AND has_sequence_privilege(r.rolname, c.oid, 'USAGE,SELECT,UPDATE')))").fetchall()
    assert rows == []
    conn.execute("CREATE TABLE later_table (id int)")  # a table created after the migration is covered too
    assert conn.execute("SELECT has_table_privilege('anon', 'later_table', 'SELECT')").fetchone() == (False,)
    conn.rollback()


def test_app_login_is_a_limited_role_with_no_password_yet(conn, db_url):
    role = conn.execute("SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolpassword IS NULL "
                        "FROM pg_authid WHERE rolname = 'app_login'").fetchone()
    assert role == (True, False, False, False, False, True)  # can log in, powerless, and no password set
    assert conn.execute("SELECT pg_has_role('app_login', 'app_runtime_role', 'MEMBER')").fetchone() == (True,)
    with psycopg.connect(make_conninfo(db_url, user="app_login")) as app:
        assert app.execute("SELECT count(*) FROM fifo_layers").fetchone() == (0,)  # no tenant set, nothing visible
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute("DELETE FROM fifo_layers")
