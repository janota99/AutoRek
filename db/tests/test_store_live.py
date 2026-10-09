"""The saved-mapping library end to end, connecting the way the deployed app does: as app_login, through
tenant_connection, with row-level security on. Runs only when DB_TEST_ADMIN_URL is set (see conftest.py)."""

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import make_conninfo  # noqa: E402

from apps.recon.custom import store  # noqa: E402
from apps.recon.custom.spec import ColumnPair, ReconSpec  # noqa: E402
from db.connection import DatabaseConfig, DatabaseConfigError, tenant_connection  # noqa: E402


@pytest.fixture
def config(db_url, org):
    return DatabaseConfig(url=make_conninfo(db_url, user="app_login"), org_id=str(org))


def make_spec(name="QB to Inf", **overrides):
    base = dict(name=name, label_a="QuickBooks", label_b="Infinium", amount=ColumnPair("AMOUNT", "OHTOTA"),
                align=[ColumnPair("P.O. NUMBER", "OHDESC", "digits")])
    base.update(overrides)
    return ReconSpec(**base)


def test_save_list_version_and_unchanged_resave(config):
    with tenant_connection(config) as conn:
        first = store.save(conn, config.org_id, make_spec(), created_by="tester")
    assert (first.version, first.created) == (1, True)
    with tenant_connection(config) as conn:
        again = store.save(conn, config.org_id, make_spec(), created_by="tester")
        changed = store.save(conn, config.org_id, make_spec(max_a_per_b=3), created_by="tester", note="allow 3 to 1")
        renamed_case = store.save(conn, config.org_id, make_spec(name="qb to inf", max_a_per_b=3), created_by="tester")
    assert (again.version, again.created) == (1, False)  # identical: nothing new is written
    assert (changed.version, changed.created) == (2, True)
    assert renamed_case.template_id == first.template_id and renamed_case.created is False  # names match ignoring case
    with tenant_connection(config) as conn:
        latest = store.list_latest(conn)
        history = store.versions(conn, first.template_id)
    assert [(m.name, m.version) for m in latest] == [("QB to Inf", 2)]
    assert ReconSpec.from_dict(latest[0].spec).max_a_per_b == 3  # the stored spec round-trips
    assert [h[0] for h in history] == [2, 1]


def test_archiving_hides_a_mapping_and_frees_the_name(config):
    with tenant_connection(config) as conn:
        outcome = store.save(conn, config.org_id, make_spec("Old name"), created_by="tester")
        store.archive(conn, outcome.template_id)
    with tenant_connection(config) as conn:
        assert store.list_latest(conn) == []
        reused = store.save(conn, config.org_id, make_spec("Old name"), created_by="tester")
    assert reused.template_id != outcome.template_id and reused.version == 1


def test_one_organization_never_sees_anothers_mappings(config, db_url):
    with psycopg.connect(db_url) as admin:  # a second organization
        other = uuid.uuid4()
        with admin.transaction():
            admin.execute("INSERT INTO organizations (org_id, name) VALUES (%s, 'Other')", (other,))
    with tenant_connection(config) as conn:
        store.save(conn, config.org_id, make_spec("Mine"), created_by="tester")
    other_config = DatabaseConfig(url=config.url, org_id=str(other))
    with tenant_connection(other_config) as conn:
        assert store.list_latest(conn) == []
    with pytest.raises(psycopg.Error):  # and cannot write into the first organization
        with tenant_connection(other_config) as conn:
            store.save(conn, config.org_id, make_spec("Sneaky"), created_by="tester")


def test_an_owner_connection_is_refused(db_url, org):
    owner = DatabaseConfig(url=db_url, org_id=str(org))  # the test server's superuser
    with pytest.raises(DatabaseConfigError):
        with tenant_connection(owner):
            pass


def test_every_version_is_listed_newest_first_and_archived_ones_disappear(config):
    with tenant_connection(config) as conn:
        a = store.save(conn, config.org_id, make_spec("Alpha"), created_by="tester")
        store.save(conn, config.org_id, make_spec("Alpha", max_a_per_b=2), created_by="tester")
        store.save(conn, config.org_id, make_spec("Alpha", max_a_per_b=4), created_by="tester")
        store.save(conn, config.org_id, make_spec("Beta"), created_by="tester")
    with tenant_connection(config) as conn:
        rows = store.list_all_versions(conn)
    assert [(r.name, r.version) for r in rows] == [("Alpha", 3), ("Alpha", 2), ("Alpha", 1), ("Beta", 1)]
    assert [ReconSpec.from_dict(r.spec).max_a_per_b for r in rows[:3]] == [4, 2, 1]  # older versions keep their own settings
    with tenant_connection(config) as conn:
        store.archive(conn, a.template_id)
    with tenant_connection(config) as conn:
        assert [(r.name, r.version) for r in store.list_all_versions(conn)] == [("Beta", 1)]
