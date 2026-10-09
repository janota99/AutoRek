import pytest

from db.migrate import Migration, MigrationError, discover, pending


def test_shipped_migrations_are_well_formed_and_ordered():
    found = discover()
    assert [m.version for m in found] == sorted(m.version for m in found)
    assert found and found[0].name == "recon_mappings_and_fifo"


def test_first_migration_creates_every_table_and_the_integrity_trigger():
    sql = discover()[0].sql
    for name in ("organizations", "recon_mapping_templates", "recon_mapping_versions", "fifo_products",
                 "fifo_period_closures", "fifo_layers", "fifo_product_period_summary",
                 "recon_mapping_latest", "fifo_current_layers", "trg_check_fifo_closure_layer_totals"):
        assert name in sql
    assert sql.count("$$") == 2  # the function body is balanced


def test_pending_skips_applied_and_refuses_edited():
    a = Migration("001", "a", "select 1", "h1")
    b = Migration("002", "b", "select 2", "h2")
    assert pending([a, b], {"001": "h1"}) == [b]
    with pytest.raises(MigrationError):
        pending([a, b], {"001": "different"})


def test_bad_file_names_and_duplicate_versions_are_rejected(tmp_path):
    (tmp_path / "oops.sql").write_text("select 1")
    with pytest.raises(MigrationError):
        discover(tmp_path)
    (tmp_path / "oops.sql").unlink()
    (tmp_path / "001_a.sql").write_text("select 1")
    (tmp_path / "001_b.sql").write_text("select 2")
    with pytest.raises(MigrationError):
        discover(tmp_path)
