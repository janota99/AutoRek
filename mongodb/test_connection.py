"""The MongoDB connection layer: settings, failure reasons, and the readiness check (no server needed)."""

import pytest

from mongodb import check_connection
from mongodb.check_samples import COLLECTIONS, load_documents
from mongodb.connection import MongoConfig, MongoConfigError, describe_error, load_config, scrub

URI = "mongodb+srv://AutoRek_app:s3cret%40pw@cluster0.abcde.mongodb.net/"


def test_nothing_configured_means_no_database():
    assert load_config(environ={}, secrets={}) is None
    assert load_config(environ={}, secrets=None) is None


def test_secrets_section_and_environment_both_work():
    from_secrets = load_config(environ={}, secrets={"mongodb": {"uri": URI}})
    assert from_secrets == MongoConfig(URI, "janota_fin")
    from_env = load_config(environ={"AUTOREK_MONGODB_URI": URI, "AUTOREK_MONGODB_DATABASE": "other"})
    assert from_env == MongoConfig(URI, "other")
    # the environment wins over the secrets file
    local = "mongodb://u:p@localhost:27017/"
    assert load_config(environ={"AUTOREK_MONGODB_URI": local}, secrets={"mongodb": {"uri": URI}}).uri == local


@pytest.mark.parametrize("bad", [
    "postgresql://u:p@h/db",                                          # the wrong kind of database
    "mongodb+srv://AutoRek_app:<password>@cluster0.x.mongodb.net/",   # placeholder left in
    "mongodb+srv://cluster0.x.mongodb.net/",                          # no credentials
])
def test_unusable_settings_are_errors_not_silently_ignored(bad):
    with pytest.raises(MongoConfigError):
        load_config(environ={}, secrets={"mongodb": {"uri": bad}})


def test_the_connection_string_is_never_shown():
    assert "s3cret" not in repr(MongoConfig(URI))
    assert "s3cret" not in scrub(f"could not connect using {URI}: refused")


def _named(name, message="", code=None):
    error = type(name, (Exception,), {})(message)
    error.code = code
    return error


def test_failures_are_explained_in_plain_language():
    assert "user name or password" in describe_error(_named("OperationFailure", "bad auth : Authentication failed.", 18))
    assert "readWrite" in describe_error(_named("OperationFailure", "not authorized on janota_fin", 13))
    assert "IP Access List" in describe_error(_named("ServerSelectionTimeoutError", "timed out"))
    assert "resolved" in describe_error(_named("ConfigurationError", "The DNS query name does not exist"))
    assert "s3cret" not in describe_error(RuntimeError(f"failed for {URI}"))


class _Collection:
    def __init__(self, docs):
        self.docs = docs

    def find(self, _query):
        return iter(self.docs)


class _Database:
    def __init__(self, data):
        self.data = data

    def list_collection_names(self):
        return list(self.data)

    def __getitem__(self, name):
        return _Collection(self.data[name])


def test_a_database_holding_the_samples_passes():
    db = _Database({name: load_documents(name) for name in COLLECTIONS})
    assert {status for status, _, _ in check_connection.check_database(db)} == {"PASS"}


def test_missing_collections_and_bad_documents_are_reported():
    data = {name: load_documents(name) for name in COLLECTIONS}
    del data["tools"]
    data["reviews"] = [dict(data["reviews"][0], rating=9)]
    rows = check_connection.check_database(_Database(data))
    statuses = {(check, status) for status, check, _ in rows}
    assert ("collection tools", "FAIL") in statuses
    assert ("reviews schema", "FAIL") in statuses


def test_run_reports_missing_configuration_without_a_driver():
    rows = check_connection.run(environ={}, secrets={})
    assert rows[0][0] == "FAIL" and "secrets.toml" in rows[0][2]


def test_run_stops_at_ping_with_the_reason_when_the_cluster_is_unreachable():
    def opener(_config):
        raise _named("ServerSelectionTimeoutError", "timed out")

    rows = check_connection.run(environ={"AUTOREK_MONGODB_URI": URI}, opener=opener)
    assert [r[1] for r in rows] == ["configuration", "ping"]
    assert rows[1][0] == "FAIL" and "IP Access List" in rows[1][2]


class _WritableDatabase:
    """Records what load() does, shaped like a pymongo Database."""

    def __init__(self, existing=None):
        self.collections = {name: list(docs) for name, docs in (existing or {}).items()}
        self.created = []
        self.dropped = []

    def list_collection_names(self):
        return list(self.collections)

    def create_collection(self, name, **options):
        assert options["validationAction"] == "error" and "$jsonSchema" in options["validator"]
        self.created.append(name)
        self.collections[name] = []

    def __getitem__(self, name):
        db = self

        class _C:
            def count_documents(self, _q):
                return len(db.collections[name])

            def insert_many(self, docs):
                db.collections[name].extend(docs)

            def drop(self):
                db.dropped.append(name)
                del db.collections[name]

        return _C()


def test_loader_creates_validated_collections_and_inserts_the_samples():
    pytest.importorskip("bson")
    from mongodb.load_samples import load

    db = _WritableDatabase()
    report = load(db)
    assert db.created == list(COLLECTIONS)
    assert {n: len(d) for n, d in db.collections.items()} == {n: len(load_documents(n)) for n in COLLECTIONS}
    assert all("inserted" in line for line in report)


def test_loader_never_overwrites_data_unless_reset():
    pytest.importorskip("bson")
    from mongodb.load_samples import load

    db = _WritableDatabase({"reviews": [{"name": "kept"}]})
    report = load(db)
    assert db.collections["reviews"] == [{"name": "kept"}] and "skipped" in report[0]
    assert db.dropped == []
    load(db, reset=True)
    assert db.dropped == list(COLLECTIONS) and len(db.collections["reviews"]) == len(load_documents("reviews"))
