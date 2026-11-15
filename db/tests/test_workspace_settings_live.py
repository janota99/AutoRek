"""Migration 005 (workspace settings and the trial balance history), connecting as the deployed app does.

The static checks always run; the live ones run only when DB_TEST_ADMIN_URL is set (see conftest.py)."""
import io

import pytest

from db.migrate import discover


def sql_005() -> str:
    found = [m for m in discover() if m.version == "005"]
    assert found and found[0].name == "workspace_settings_and_trial_balance"
    return found[0].sql


def test_005_is_tenant_scoped_and_the_trial_balance_history_is_append_only():
    sql = sql_005()
    for table in ("workspace_settings", "sales_tax_trial_balance_versions"):
        assert f"CREATE TABLE {table} " in sql
        assert f"ALTER TABLE {table}" in sql and "FORCE ROW LEVEL SECURITY" in sql
        assert f"CREATE POLICY tenant_isolation ON {table}" in sql
    assert "ON sales_tax_trial_balance_versions\n    FOR EACH ROW EXECUTE FUNCTION forbid_history_change()" in sql
    assert "GRANT DELETE" not in sql and "ON DELETE CASCADE" not in sql
    assert "REVOKE DELETE ON workspace_settings, sales_tax_trial_balance_versions" in sql


psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import make_conninfo  # noqa: E402

from apps.sales_tax import ingestion, trial_balance_store  # noqa: E402
from db.connection import DatabaseConfig, tenant_connection  # noqa: E402
from shared import settings_store  # noqa: E402


@pytest.fixture
def config(db_url, org):
    return DatabaseConfig(url=make_conninfo(db_url, user="app_login"), org_id=str(org))


def workbook(*rows) -> bytes:
    import pandas as pd

    buffer = io.BytesIO()
    pd.DataFrame(list(rows), columns=["Entity", "Account Name", "GL Account"]).to_excel(buffer, index=False)
    return buffer.getvalue()


def test_settings_round_trip_and_replace_in_place(config):
    with tenant_connection(config) as conn:
        assert settings_store.get(conn, "fiscal") is None
        settings_store.put(conn, config.org_id, "fiscal", {"year": 2026}, updated_by="pat")
        settings_store.put(conn, config.org_id, "fiscal", {"year": 2027}, updated_by="sam")
    with tenant_connection(config) as conn:
        assert settings_store.get(conn, "fiscal") == {"year": 2027}
        assert conn.execute("SELECT count(*), max(updated_by) FROM workspace_settings").fetchone() == (1, "sam")


def test_settings_are_invisible_to_another_organization(config, conn):
    import uuid
    with tenant_connection(config) as c:
        settings_store.put(c, config.org_id, "fiscal", {"year": 2026})
    other = uuid.uuid4()
    with conn.transaction():
        conn.execute("INSERT INTO organizations (org_id, name) VALUES (%s, 'Other Co')", (other,))
    with tenant_connection(DatabaseConfig(config.url, str(other))) as c:
        assert settings_store.get(c, "fiscal") is None


def test_the_app_role_cannot_delete_settings_or_touch_trial_balance_history(config):
    with tenant_connection(config) as conn:
        settings_store.put(conn, config.org_id, "fiscal", {"year": 2026})
        trial_balance_store.save(conn, config.org_id, b"x" * 10)
    for statement in ("DELETE FROM workspace_settings", "UPDATE sales_tax_trial_balance_versions SET saved_by = 'x'",
                      "DELETE FROM sales_tax_trial_balance_versions"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_connection(config) as conn:
                conn.execute(statement)


def test_history_current_backup_restore_and_clear(config):
    org = config.org_id
    with tenant_connection(config) as conn:
        assert trial_balance_store.current_version(conn) is None
        first, _ = trial_balance_store.save(conn, org, b"first")
        assert trial_balance_store.backup_version(conn) is None
        second, _ = trial_balance_store.save(conn, org, b"second")
        assert trial_balance_store.current_version(conn).version_id == second
        assert trial_balance_store.backup_version(conn).version_id == first
        trial_balance_store.save(conn, org, trial_balance_store.read_file(conn, first), kind="restore")
        assert trial_balance_store.read_file(conn, trial_balance_store.current_version(conn).version_id) == b"first"
        assert trial_balance_store.read_file(conn, trial_balance_store.backup_version(conn).version_id) == b"second"
        assert trial_balance_store.clear(conn, org) == 2
        assert trial_balance_store.current_version(conn) is None and trial_balance_store.backup_version(conn) is None
        assert trial_balance_store.clear(conn, org) == 0          # nothing left to hide, so nothing is written
        trial_balance_store.save(conn, org, b"after")
        assert trial_balance_store.backup_version(conn) is None   # a clear hides what came before it
        assert conn.execute("SELECT count(*) FROM sales_tax_trial_balance_versions").fetchone()[0] == 5


def test_clear_current_keeps_the_backup(config):
    org = config.org_id
    with tenant_connection(config) as conn:
        first, _ = trial_balance_store.save(conn, org, b"first")
        trial_balance_store.save(conn, org, b"second")
        assert trial_balance_store.clear(conn, org, include_backup=False) == 1
        assert trial_balance_store.current_version(conn) is None
        assert trial_balance_store.backup_version(conn).version_id == first


def test_the_sales_tax_cache_works_end_to_end_from_the_database(config, tmp_path, monkeypatch):
    monkeypatch.setattr(ingestion, "TRIAL_BALANCE_CACHE_PATH", tmp_path / "tb.xlsx")   # proves the disk is not used
    monkeypatch.setattr(ingestion, "TRIAL_BALANCE_BACKUP_PATH", tmp_path / "tb.backup.xlsx")
    ingestion.configure_database(lambda: config, lambda: "pat@example.com")
    try:
        ingestion._forget_snapshot()
        assert not ingestion.get_trial_balance_cache_status().exists
        one = ingestion.save_trial_balance_cache(workbook(("", "Cash", "A-1"),))
        two = ingestion.save_trial_balance_cache(workbook(("", "Cash", "A-1"), ("", "Bank", "B-2")))
        df, status = ingestion.load_trial_balance_cache_snapshot()
        assert status.valid and status.row_count == 2 and status.fingerprint == two.fingerprint and len(df) == 2
        assert not (tmp_path / "tb.xlsx").exists()
        restored = ingestion.restore_trial_balance_cache_backup()
        assert restored.fingerprint == one.fingerprint and ingestion.get_trial_balance_cache_status().row_count == 1
        with pytest.raises(ingestion.IngestionError):
            ingestion.save_trial_balance_cache(b"junk")                              # a bad file changes nothing
        assert ingestion.get_trial_balance_cache_status().fingerprint == one.fingerprint
        assert ingestion.clear_trial_balance_cache() == 2
        assert not ingestion.get_trial_balance_cache_status().exists
        with tenant_connection(config) as conn:
            assert {r[0] for r in conn.execute("SELECT saved_by FROM sales_tax_trial_balance_versions")} == {"pat@example.com"}
    finally:
        ingestion.configure_database(lambda: None)
        ingestion._forget_snapshot()


def test_an_unreachable_database_is_an_error_never_a_silent_disk_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(ingestion, "TRIAL_BALANCE_CACHE_PATH", tmp_path / "tb.xlsx")
    (tmp_path / "tb.xlsx").write_bytes(workbook(("", "Cash", "A-1"),))     # a good file on disk must not be shown instead
    dead = DatabaseConfig("postgresql://app_login@127.0.0.1:1/none?connect_timeout=1", "00000000-0000-0000-0000-000000000001")
    ingestion.configure_database(lambda: dead)
    try:
        ingestion._forget_snapshot()
        df, status = ingestion.load_trial_balance_cache_snapshot()
        assert df is None and not status.valid and "could not be reached" in status.error
        with pytest.raises(ingestion.IngestionError):
            ingestion.save_trial_balance_cache(workbook(("", "Cash", "A-1"),))
    finally:
        ingestion.configure_database(lambda: None)


def test_the_fiscal_calendar_is_saved_in_and_read_from_the_database_not_a_file(config, tmp_path, monkeypatch):
    from datetime import date

    from shared import database, fiscal_session
    from shared.fiscal_calendar import default_calendar

    monkeypatch.setenv("FISCAL_SETTINGS_PATH", str(tmp_path / "fiscal.json"))
    monkeypatch.setattr(database, "config", lambda: config)
    state = {}
    monkeypatch.setattr(fiscal_session.st, "session_state", state)
    calendar = default_calendar(2031)
    fiscal_session.set_calendar(calendar)
    fiscal_session.set_end_date(date(2031, 3, 1))
    assert not (tmp_path / "fiscal.json").exists() and not fiscal_session.save_failed()
    state.clear()                                                    # a new session, as after a restart or on another device
    assert fiscal_session.get_calendar() == calendar and fiscal_session.get_end_date() == date(2031, 3, 1)
    assert 2031 in fiscal_session.saved_years()


def test_an_unreadable_fiscal_database_is_reported_and_never_written_over(config, tmp_path, monkeypatch):
    from shared import database, fiscal_session
    from shared.fiscal_calendar import default_calendar

    dead = DatabaseConfig("postgresql://app_login@127.0.0.1:1/none?connect_timeout=1", config.org_id)
    monkeypatch.setenv("FISCAL_SETTINGS_PATH", str(tmp_path / "fiscal.json"))
    monkeypatch.setattr(database, "config", lambda: dead)
    monkeypatch.setattr(fiscal_session, "DB_WAIT_SECONDS", 1)
    monkeypatch.setattr(fiscal_session.st, "session_state", {})
    fiscal_session.get_calendar()
    assert fiscal_session.save_problem() and "could not be read" in fiscal_session.save_problem()
    fiscal_session.set_calendar(default_calendar(2032))
    assert not (tmp_path / "fiscal.json").exists()                   # no silent fall back to a file either
