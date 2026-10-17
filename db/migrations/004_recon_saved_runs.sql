-- Saved reconciliation runs and their audit log.
--
--   recon_runs            one row per finished run: who, when, versions, headline figures, controls, mapping used.
--   recon_run_inputs      the fingerprint (SHA-256, file name, worksheet) of every uploaded file; never the file itself.
--   recon_run_events      the append-only log after the run: notes, "reviewed" marks, workpaper prepared/downloaded.
--   recon_run_workpapers  the Excel workpapers built for a run, kept so they can be downloaded again.
--
-- All four are append-only (no update, no delete, for the app role or anyone else but the owner), carry the same
-- row-level security as migration 002, and never change a match, a number, or a workbook cell: they only record them.
-- `ran_by_verified` / `actor_verified` say whether the name came from a real sign-in (true) or from the unverified
-- browser sign-in (false), so the log never overstates who did what.

CREATE TABLE recon_runs (
    run_id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                 uuid NOT NULL REFERENCES organizations(org_id) ON DELETE RESTRICT,
    run_ref                text NOT NULL CHECK (length(btrim(run_ref)) > 0),   -- the engine's id, REC-YYYYMMDD-HHMMSS-XXXXXXXX
    ran_at                 timestamptz NOT NULL,
    recorded_at            timestamptz NOT NULL DEFAULT now(),
    ran_by                 text NOT NULL CHECK (length(btrim(ran_by)) > 0),
    ran_by_verified        boolean NOT NULL DEFAULT false,
    fiscal_year            smallint,
    fiscal_period          smallint CHECK (fiscal_period IS NULL OR fiscal_period BETWEEN 1 AND 13),
    app_version            text NOT NULL,
    matching_rule_version  text NOT NULL,
    control_status         text NOT NULL CHECK (control_status IN ('PASS', 'FAIL')),
    posting_status         text,
    headline               jsonb NOT NULL CHECK (jsonb_typeof(headline) = 'object'),
    controls               jsonb NOT NULL CHECK (jsonb_typeof(controls) = 'array'),
    mapping                jsonb NOT NULL CHECK (jsonb_typeof(mapping) = 'object'),
    record_sha256          text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),  -- digest of the content above, to show it was not edited
    CONSTRAINT recon_runs_org_run_uq UNIQUE (org_id, run_id),
    CONSTRAINT recon_runs_org_ref_uq UNIQUE (org_id, run_ref)
);
CREATE INDEX recon_runs_org_recorded_idx ON recon_runs (org_id, recorded_at DESC);

CREATE TABLE recon_run_inputs (
    input_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL,
    run_id      uuid NOT NULL,
    role        text NOT NULL CHECK (role IN ('qb', 'infinium', 'qb_historical', 'infinium_historical')),
    filename    text NOT NULL,
    sheet_name  text,
    sha256      text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (run_id, role),
    CONSTRAINT fk_recon_inputs_run FOREIGN KEY (org_id, run_id) REFERENCES recon_runs(org_id, run_id) ON DELETE RESTRICT
);

CREATE TABLE recon_run_events (
    event_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq             bigint GENERATED ALWAYS AS IDENTITY,   -- strict order, even for two events in one transaction
    org_id          uuid NOT NULL,
    run_id          uuid NOT NULL,
    event_type      text NOT NULL CHECK (event_type IN ('note', 'reviewed', 'workpaper_prepared', 'workpaper_downloaded')),
    actor           text NOT NULL CHECK (length(btrim(actor)) > 0),
    actor_verified  boolean NOT NULL DEFAULT false,
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail) = 'object'),
    created_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT fk_recon_events_run FOREIGN KEY (org_id, run_id) REFERENCES recon_runs(org_id, run_id) ON DELETE RESTRICT
);
CREATE INDEX recon_run_events_run_idx ON recon_run_events (run_id, created_at);

CREATE TABLE recon_run_workpapers (
    workpaper_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL,
    run_id        uuid NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('primary', 'legacy')),
    filename      text NOT NULL,
    sha256        text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes    integer NOT NULL CHECK (size_bytes > 0),
    content       bytea NOT NULL,
    created_by    text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT fk_recon_workpapers_run FOREIGN KEY (org_id, run_id) REFERENCES recon_runs(org_id, run_id) ON DELETE RESTRICT
);
CREATE INDEX recon_run_workpapers_run_idx ON recon_run_workpapers (run_id, created_at DESC);

-- Append-only, using the guard from migration 002.
CREATE TRIGGER trg_recon_runs_append_only BEFORE UPDATE OR DELETE ON recon_runs
    FOR EACH ROW EXECUTE FUNCTION forbid_history_change();
CREATE TRIGGER trg_recon_inputs_append_only BEFORE UPDATE OR DELETE ON recon_run_inputs
    FOR EACH ROW EXECUTE FUNCTION forbid_history_change();
CREATE TRIGGER trg_recon_events_append_only BEFORE UPDATE OR DELETE ON recon_run_events
    FOR EACH ROW EXECUTE FUNCTION forbid_history_change();
CREATE TRIGGER trg_recon_workpapers_append_only BEFORE UPDATE OR DELETE ON recon_run_workpapers
    FOR EACH ROW EXECUTE FUNCTION forbid_history_change();

-- Row-level security, keyed on app.org_id exactly as in migration 002.
ALTER TABLE recon_runs             ENABLE ROW LEVEL SECURITY;
ALTER TABLE recon_run_inputs       ENABLE ROW LEVEL SECURITY;
ALTER TABLE recon_run_events       ENABLE ROW LEVEL SECURITY;
ALTER TABLE recon_run_workpapers   ENABLE ROW LEVEL SECURITY;
ALTER TABLE recon_runs             FORCE ROW LEVEL SECURITY;
ALTER TABLE recon_run_inputs       FORCE ROW LEVEL SECURITY;
ALTER TABLE recon_run_events       FORCE ROW LEVEL SECURITY;
ALTER TABLE recon_run_workpapers   FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON recon_runs
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
CREATE POLICY tenant_isolation ON recon_run_inputs
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
CREATE POLICY tenant_isolation ON recon_run_events
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
CREATE POLICY tenant_isolation ON recon_run_workpapers
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);

-- The runtime role reads and inserts; it can never update or delete (migration 003's hardening of the Supabase web
-- roles also applies, because their default privileges were revoked for future tables).
GRANT SELECT, INSERT ON recon_runs, recon_run_inputs, recon_run_events, recon_run_workpapers TO app_runtime_role;
GRANT USAGE ON SEQUENCE recon_run_events_seq_seq TO app_runtime_role;
REVOKE UPDATE, DELETE ON recon_runs, recon_run_inputs, recon_run_events, recon_run_workpapers FROM app_runtime_role;
