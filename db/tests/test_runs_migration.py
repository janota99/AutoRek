"""Static checks on migration 004 (saved runs); the live rules are exercised in test_runs_live.py."""
from db.migrate import discover

TABLES = ("recon_runs", "recon_run_inputs", "recon_run_events", "recon_run_workpapers")


def sql_004():
    found = [m for m in discover() if m.version == "004"]
    assert found and found[0].name == "recon_saved_runs"
    return found[0].sql


def test_every_run_table_is_append_only_tenant_scoped_and_never_updatable_by_the_app():
    sql = sql_004()
    for table in TABLES:
        assert f"CREATE TABLE {table} " in sql
        assert f"ON {table}\n    FOR EACH ROW EXECUTE FUNCTION forbid_history_change()" in sql
        assert f"ALTER TABLE {table}" in sql and "FORCE ROW LEVEL SECURITY" in sql
        assert f"CREATE POLICY tenant_isolation ON {table}" in sql
    assert "REVOKE UPDATE, DELETE ON recon_runs, recon_run_inputs, recon_run_events, recon_run_workpapers" in sql
    assert "GRANT UPDATE" not in sql and "GRANT DELETE" not in sql and "ON DELETE CASCADE" not in sql


def test_a_run_cannot_be_stored_without_a_verdict_or_a_digest():
    sql = sql_004()
    assert "CHECK (control_status IN ('PASS', 'FAIL'))" in sql
    assert "CHECK (record_sha256 ~ '^[0-9a-f]{64}$')" in sql
