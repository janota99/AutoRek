-- Workspace settings and the Sales Tax trial balance, kept per organization in PostgreSQL instead of on the app server's disk.
--
--   workspace_settings               one JSON document per organization and name (today: 'fiscal', the Workspace's fiscal calendars
--                                    and period-end date). Editable in place; never deleted by the app role.
--   sales_tax_trial_balance_versions every trial balance that was uploaded, restored or cleared, newest last. Append-only, so the
--                                    cache has a history: the newest row is the current trial balance, the row before it is
--                                    the one-version backup, and a 'clear' row hides everything before it (the file is never
--                                    edited, which is what makes "restore" and "who changed it" answerable).
--
-- Both carry the same row-level security as migration 002, keyed on app.org_id. The app role can read and insert; only
-- workspace_settings can be updated, and neither table can be deleted from by the app role.

CREATE TABLE workspace_settings (
    org_id      uuid NOT NULL REFERENCES organizations(org_id) ON DELETE RESTRICT,
    name        text NOT NULL CHECK (name ~ '^[a-z][a-z0-9_]{0,63}$'),
    value       jsonb NOT NULL CHECK (jsonb_typeof(value) = 'object'),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  text NOT NULL DEFAULT 'app' CHECK (length(btrim(updated_by)) > 0),
    PRIMARY KEY (org_id, name)
);

CREATE TABLE sales_tax_trial_balance_versions (
    version_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   -- strict order, even for two rows in one transaction
    org_id      uuid NOT NULL REFERENCES organizations(org_id) ON DELETE RESTRICT,
    kind        text NOT NULL CHECK (kind IN ('upload', 'restore', 'clear_all', 'clear_current')),
    content     bytea,
    sha256      text,
    size_bytes  integer,
    saved_by    text NOT NULL DEFAULT 'app' CHECK (length(btrim(saved_by)) > 0),
    created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    -- A workbook row carries the file and its digest; a clear row carries neither.
    CONSTRAINT trial_balance_content_by_kind CHECK (
        (kind IN ('upload', 'restore') AND content IS NOT NULL AND size_bytes > 0 AND sha256 ~ '^[0-9a-f]{64}$'
            AND length(content) = size_bytes)
        OR (kind IN ('clear_all', 'clear_current') AND content IS NULL AND sha256 IS NULL AND size_bytes IS NULL))
);
CREATE INDEX trial_balance_versions_org_idx ON sales_tax_trial_balance_versions (org_id, version_id DESC);

CREATE TRIGGER trg_trial_balance_append_only BEFORE UPDATE OR DELETE ON sales_tax_trial_balance_versions
    FOR EACH ROW EXECUTE FUNCTION forbid_history_change();

ALTER TABLE workspace_settings                ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_tax_trial_balance_versions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_settings                FORCE ROW LEVEL SECURITY;
ALTER TABLE sales_tax_trial_balance_versions  FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON workspace_settings
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
CREATE POLICY tenant_isolation ON sales_tax_trial_balance_versions
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE ON workspace_settings TO app_runtime_role;
GRANT SELECT, INSERT ON sales_tax_trial_balance_versions TO app_runtime_role;
REVOKE DELETE ON workspace_settings, sales_tax_trial_balance_versions FROM app_runtime_role;
REVOKE UPDATE ON sales_tax_trial_balance_versions FROM app_runtime_role;
