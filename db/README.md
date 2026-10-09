# Database (PostgreSQL)

Schema for saved reconciliation mappings and the FIFO period/layer store. Only the Custom mapping library uses it so far (`apps/recon/custom/store.py`); FIFO still uses its JSON snapshots. The app runs without a database configured.

| File | What it is |
|---|---|
| `migrations/001_recon_mappings_and_fifo.sql` | Tables, composite tenant keys, views, deferred layer-total check. |
| `migrations/002_integrity_and_tenant_security.sql` | Sequence, rollforward and latest-only-reopen triggers; tolerance on layer totals; append-only history; no cascading deletes; row-level security; the `app_runtime_role` grants. |
| `migrations/003_supabase_hardening_and_app_login.sql` | Takes Supabase's `anon` and `authenticated` roles' access away (now and by default for new tables); creates `app_login`, the role the deployed app connects as. It has no password until you set one by hand. |
| `connection.py` | Reads the app's database settings and opens a connection scoped to one organization; refuses an owner connection. |
| `migrate.py` | Applies new migration files once each (`py -m db.migrate`, `--status`). Reads `DATABASE_URL`. |
| `tests/test_connection.py` | Settings handling; always runs. |
| `tests/test_store_live.py` | The saved-mapping library through the real app role; needs `DB_TEST_ADMIN_URL`. |
| `tests/test_migrate.py` | File checks; always runs. |
| `tests/test_schema_live.py` | Applies the migrations to a throwaway database and checks every rule; runs only when `DB_TEST_ADMIN_URL` is set. |

```powershell
pip install "psycopg[binary]"
$env:DB_TEST_ADMIN_URL = "postgresql://postgres@127.0.0.1:55432/postgres"   # a server you may create databases on
py -m pytest db
```

Rules to keep: never edit a migration that has been applied (add `003_...sql`); never commit a connection string; the
app must run `SET LOCAL app.org_id = '<uuid>'` at the start of each transaction (with none set, no rows are visible).
Tolerances in `check_fifo_closure_layer_totals` mirror `QTY_TOLERANCE` and `VALUE_TOLERANCE` in
`apps/fifo_inventory/user_inputs.py`; change both together.
