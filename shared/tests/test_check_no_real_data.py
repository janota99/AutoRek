from shared.check_no_real_data import violations


def test_flags_real_data_and_secrets():
    paths = [
        "apps/recon/data/sales.xlsx",
        "apps/fifo_inventory/fifo_snapshots/P01.json",
        "apps/fifo_inventory/app_settings.json",
        ".streamlit/secrets.toml",
        "deploy/.env",
        "keys/server.pem",
    ]
    assert violations(paths) == sorted(paths)


def test_allows_normal_files():
    paths = [
        "apps/recon/matching/core.py",
        "apps/recon/tests/data_helpers.py",
        ".streamlit/secrets.toml.example",
        ".env.example",
        "sample_data/demo.xlsx",
        "apps/fifo_inventory/app_settings_schema.py",
    ]
    assert violations(paths) == []


def test_windows_separators():
    assert violations(["apps\\sales_tax\\data\\x.csv"]) == ["apps/sales_tax/data/x.csv"]
