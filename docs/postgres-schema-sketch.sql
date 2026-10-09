-- SKETCH ONLY. Nothing in the app reads or runs this yet. It is a starting point for review.
--
-- Covers two areas:
--   1. Saved reconciliation mappings (apps/recon/custom: ReconSpec)
--   3. FIFO periods and layers (apps/fifo_inventory: FIFOLayerStore snapshots)
--
-- Conventions
--   * Every table carries org_id so one database can hold many customers. Turn on row-level security on each
--     table and set `app.org_id` per connection:  USING (org_id = current_setting('app.org_id')::uuid)
--   * "Never silently change the books": history is append-only. Rows are superseded or marked reopened,
--     never updated in place or deleted (revoke UPDATE/DELETE from the app role on the immutable tables).
--   * Money is NUMERIC, never float. Receipt value (total_value) is authoritative; unit cost is derived.
--   * `actor` is plain text until real sign-in exists (today's sign-in is simulated in the browser); swap it for a
--     users(id) foreign key when accounts are built.

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

CREATE TABLE organizations (
    org_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ============================================================================================================
-- 1. SAVED RECONCILIATION MAPPINGS
--    A template is a named thing; each save adds an immutable version. A run (future item 2) can point at the
--    exact version it used, so "what mapping produced this result" is always answerable.
-- ============================================================================================================

CREATE TABLE recon_mapping_templates (
    template_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL REFERENCES organizations,
    name         text NOT NULL CHECK (length(btrim(name)) > 0),
    description  text,
    category     text NOT NULL DEFAULT 'Custom',          -- 'Accounting / ERP', 'Custom', ...
    visibility   text NOT NULL DEFAULT 'org' CHECK (visibility IN ('private', 'org')),
    created_by   text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    archived_at  timestamptz                               -- archive instead of delete
);
CREATE UNIQUE INDEX recon_templates_name_uq ON recon_mapping_templates (org_id, lower(name)) WHERE archived_at IS NULL;

CREATE TABLE recon_mapping_versions (
    version_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id     uuid NOT NULL REFERENCES recon_mapping_templates,
    org_id          uuid NOT NULL REFERENCES organizations,
    version         integer NOT NULL CHECK (version >= 1),
    -- The ReconSpec exactly as spec.py serializes it (column NAMES only, never data). jsonb so the spec can grow
    -- without a migration; spec_version says which shape this row holds (ReconSpec.version).
    spec            jsonb NOT NULL,
    spec_version    integer NOT NULL,
    spec_sha256     text NOT NULL,                         -- hash of the canonical JSON, for "did anything change"
    -- Pulled out of the JSON so they can be searched and constrained:
    label_a         text NOT NULL,
    label_b         text NOT NULL,
    max_a_per_b     smallint NOT NULL CHECK (max_a_per_b BETWEEN 1 AND 6),   -- MAX_GROUP_SIZE in spec.py
    max_b_per_a     smallint NOT NULL CHECK (max_b_per_a BETWEEN 1 AND 6),
    align_count     smallint NOT NULL CHECK (align_count BETWEEN 0 AND 8),
    change_note     text,
    created_by      text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (template_id, version),
    CHECK (jsonb_typeof(spec) = 'object' AND label_a <> label_b)
);
-- Immutable: add a new version instead of editing one (enforce with a trigger or by revoking UPDATE/DELETE).

CREATE VIEW recon_mapping_latest AS
SELECT DISTINCT ON (v.template_id) t.org_id, t.template_id, t.name, t.category, t.visibility, v.version, v.spec,
       v.label_a, v.label_b, v.created_at AS version_created_at
FROM recon_mapping_versions v JOIN recon_mapping_templates t USING (template_id)
WHERE t.archived_at IS NULL
ORDER BY v.template_id, v.version DESC;

-- ============================================================================================================
-- 3. FIFO PERIODS AND LAYERS
--    Replaces fifo_snapshots/*.json and the in-session layer store. There is NO mutable "current layers" table:
--    the official layers are the ENDING layers of the latest closed period (see the view). Closing a period is one
--    transaction; reopening marks the latest closure reopened and leaves its rows for the audit trail.
-- ============================================================================================================

CREATE TABLE fifo_products (
    org_id      uuid NOT NULL REFERENCES organizations,
    alias       smallint NOT NULL,                         -- the integer product alias the engine uses today
    code        text,                                      -- e.g. W0082
    name        text NOT NULL,
    PRIMARY KEY (org_id, alias)
);

CREATE TABLE fifo_period_closures (
    closure_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                uuid NOT NULL REFERENCES organizations,
    fiscal_year           smallint NOT NULL CHECK (fiscal_year BETWEEN 2000 AND 2100),
    period                smallint NOT NULL CHECK (period BETWEEN 1 AND 13),      -- 13-period calendar
    ordinal               integer GENERATED ALWAYS AS (fiscal_year * 13 + period) STORED,  -- sequence check uses this
    status                text NOT NULL DEFAULT 'closed' CHECK (status IN ('closed', 'reopened')),
    -- Provenance: which engine, which snapshot shape, which inputs. Hashes only; uploads are not stored here.
    engine_version        text NOT NULL,                   -- ENGINE_VERSION (bump on any calculation change)
    snapshot_schema_ver   smallint NOT NULL,               -- SNAPSHOT_SCHEMA_VERSION
    run_signature         text NOT NULL,                   -- the preview's run signature
    master_grid_sha256    text,
    receipts_sha256       text,
    control_counts        jsonb NOT NULL,                  -- PASS / REVIEW / FAIL counts
    ending_qty            numeric(24, 4) NOT NULL,         -- control totals, checked against the layers below
    ending_value          numeric(18, 2) NOT NULL,
    review_acknowledged   boolean NOT NULL DEFAULT false, -- REVIEW items were acknowledged at close
    closed_by             text NOT NULL,
    closed_at             timestamptz NOT NULL DEFAULT now(),
    reopened_by           text,
    reopened_at           timestamptz,
    CHECK ((status = 'reopened') = (reopened_at IS NOT NULL))
);
-- One live closure per period; reopened rows stay as history.
CREATE UNIQUE INDEX fifo_one_closed_per_period ON fifo_period_closures (org_id, fiscal_year, period) WHERE status = 'closed';

CREATE TABLE fifo_layers (
    layer_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id        uuid NOT NULL,
    closure_id    uuid NOT NULL REFERENCES fifo_period_closures,
    alias         smallint NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('beginning', 'ending')),
    layer_seq     integer NOT NULL CHECK (layer_seq >= 1),   -- oldest first: strict FIFO order is the sequence
    received      text NOT NULL,                             -- the layer's date label as entered ("2026-06-02", "PD11-26: ...")
    qty           numeric(24, 4) NOT NULL CHECK (qty >= 0),  -- never a negative layer
    unit_cost     numeric(20, 12) NOT NULL CHECK (unit_cost >= 0),
    total_value   numeric(18, 2) NOT NULL CHECK (total_value >= 0),   -- authoritative extended value
    UNIQUE (closure_id, kind, alias, layer_seq),
    FOREIGN KEY (org_id, alias) REFERENCES fifo_products
);

-- Per-product rollforward for Trends and Product Lookup (today: product_period_summary in the snapshot).
CREATE TABLE fifo_product_period_summary (
    closure_id     uuid NOT NULL REFERENCES fifo_period_closures,
    org_id         uuid NOT NULL,
    alias          smallint NOT NULL,
    beginning_qty  numeric(24, 4) NOT NULL,  beginning_value numeric(18, 2) NOT NULL,
    received_qty   numeric(24, 4) NOT NULL,  received_value  numeric(18, 2) NOT NULL,
    used_qty       numeric(24, 4) NOT NULL,  used_value      numeric(18, 2) NOT NULL,
    ending_qty     numeric(24, 4) NOT NULL,  ending_value    numeric(18, 2) NOT NULL,
    detail         jsonb,                    -- anything else the engine reports; shape follows engine_version
    PRIMARY KEY (closure_id, alias),
    FOREIGN KEY (org_id, alias) REFERENCES fifo_products
);

-- The official layers right now: ending layers of the latest closed period.
CREATE VIEW fifo_current_layers AS
SELECT l.*
FROM fifo_layers l
JOIN fifo_period_closures c USING (closure_id)
WHERE l.kind = 'ending' AND c.status = 'closed'
  AND c.ordinal = (SELECT max(ordinal) FROM fifo_period_closures x WHERE x.org_id = c.org_id AND x.status = 'closed');

-- Rules the application enforces today, restated as database guarantees (write these as triggers):
--   * Periods close in sequence: a new closure's ordinal must be max(closed ordinal) + 1. Because ordinal is
--     fiscal_year*13 + period, P13 of one year is followed by P1 of the next with no special case. The first closure
--     in an organization is the opening seed (FY2026 P12 today).
--   * Only the latest closed period can be reopened.
--   * Each closure's ending layers must sum to ending_qty / ending_value (a DEFERRABLE INITIALLY DEFERRED constraint
--     trigger, checked at COMMIT, within the existing VALUE_TOLERANCE of the engine).
--   * A closure's beginning layers must equal the previous closure's ending layers.
--
-- Closing a period (replaces commit_period + the snapshot file write), all in ONE transaction:
--   BEGIN;
--     INSERT INTO fifo_period_closures (...);
--     INSERT INTO fifo_layers (... 'beginning' ...), (... 'ending' ...);
--     INSERT INTO fifo_product_period_summary (...);
--   COMMIT;   -- the deferred checks above run here; any failure rolls back the whole close
--
-- Reopening: UPDATE the latest closure SET status='reopened', reopened_by=..., reopened_at=now(). Nothing is deleted.
-- fifo_current_layers then points at the previous period's ending layers automatically.
--
-- Settings (app_settings.json today): a small table, e.g. fifo_settings(org_id, key text, value jsonb,
-- updated_by text, updated_at timestamptz, PRIMARY KEY (org_id, key)).
--
-- Migration: read each fifo_snapshots/*.json (schema_version 1 or 2), insert one closure per history record, and
-- keep the raw file in a fifo_snapshot_imports(org_id, filename, payload jsonb, imported_at) table so the move is
-- traceable. The P12 seed layers (PERIOD_12_OPENING_LAYERS) load as the opening closure.
