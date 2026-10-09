import pytest

from db.connection import DatabaseConfig, DatabaseConfigError, load_config

ORG = "3f9c1b7e-6a24-4d5b-9c8e-2e51a7d04b36"


def test_nothing_configured_means_no_database():
    assert load_config(environ={}, secrets={}) is None
    assert load_config(environ={}, secrets=None) is None


def test_secrets_section_and_environment_both_work():
    from_secrets = load_config(environ={}, secrets={"database": {"url": "postgresql://u:p@h/db", "org_id": ORG}})
    assert from_secrets == DatabaseConfig("postgresql://u:p@h/db", ORG)
    from_env = load_config(environ={"AUTOREK_APP_DATABASE_URL": "postgresql://u:p@h/db", "AUTOREK_ORG_ID": ORG})
    assert from_env == from_secrets


def test_the_owner_migration_variable_is_never_read():
    with pytest.raises(DatabaseConfigError):  # an org without an app url is a mistake, not "not configured"
        load_config(environ={"DATABASE_URL": "postgresql://owner:pw@h/db", "AUTOREK_ORG_ID": ORG})
    assert load_config(environ={"DATABASE_URL": "postgresql://owner:pw@h/db"}) is None


def test_half_configured_and_bad_ids_are_errors():
    with pytest.raises(DatabaseConfigError):
        load_config(environ={}, secrets={"database": {"url": "postgresql://u:p@h/db"}})
    with pytest.raises(DatabaseConfigError):
        load_config(environ={}, secrets={"database": {"url": "postgresql://u:p@h/db", "org_id": "not-a-uuid"}})


def test_the_connection_string_is_never_in_the_repr():
    assert "secretpw" not in repr(DatabaseConfig("postgresql://u:secretpw@h/db", ORG))


def test_error_text_shown_to_users_never_contains_a_connection_string():
    pytest.importorskip("streamlit")
    from apps.recon.custom.ui import _why
    shown = _why(RuntimeError('could not connect using postgresql://app_login.x:SECRET@host:5432/postgres: refused'))
    assert "SECRET" not in shown and "RuntimeError" in shown
    assert "hunter2" not in _why(RuntimeError("bad password=hunter2 here"))
