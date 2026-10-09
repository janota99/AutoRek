-- Production DDL Revision: Reconciliation Mappings & FIFO Layer Store
-- Incorporates multi-tenant isolation fixes, optimized indexing, and deferrable check triggers.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================================================================
-- ORGANIZATIONS (TENANTS)
-- ============================================================================================================

CREATE TABLE organizations (
    org_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ============================================================================================================
-- 1. SAVED RECONCILIATION MAPPINGS
-- ============================================================================================================

CREATE TABLE recon_mapping_templates (
    template_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    name         text NOT NULL CHECK (length(btrim(name)) > 0),
    description  text,
    category     text NOT NULL DEFAULT 'Custom',
    visibility   text NOT NULL DEFAULT 'org' CHECK (visibility IN ('private', 'org')),
    created_by   text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    archived_at  timestamptz,
    -- Unique constraint supporting multi-tenant FK on versions
    CONSTRAINT recon_templates_org_id_template_id_uq UNIQUE (org_id, template_id)
);

CREATE UNIQUE INDEX recon_templates_name_uq
    ON recon_mapping_templates (org_id, lower(name))
    WHERE archived_at IS NULL;

CREATE TABLE recon_mapping_versions (
    version_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id   uuid NOT NULL,
    org_id        uuid NOT NULL,
    version       integer NOT NULL CHECK (version >= 1),
    spec          jsonb NOT NULL,
    spec_version  integer NOT NULL,
    spec_sha256   text NOT NULL,
    label_a       text NOT NULL,
    label_b       text NOT NULL,
    max_a_per_b   smallint NOT NULL CHECK (max_a_per_b BETWEEN 1 AND 6),
    max_b_per_a   smallint NOT NULL CHECK (max_b_per_a BETWEEN 1 AND 6),
    align_count   smallint NOT NULL CHECK (align_count BETWEEN 0 AND 8),
    change_note   text,
    created_by    text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),

    UNIQUE (template_id, version),
    CHECK (jsonb_typeof(spec) = 'object' AND label_a <> label_b),

    -- Bound to org_id + template_id to prevent cross-tenant version references
    CONSTRAINT fk_recon_versions_template
        FOREIGN KEY (org_id, template_id)
        REFERENCES recon_mapping_templates(org_id, template_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_recon_versions_lookup
    ON recon_mapping_versions (template_id, version DESC);

CREATE VIEW recon_mapping_latest AS
SELECT DISTINCT ON (v.template_id)
    t.org_id,
    t.template_id,
    t.name,
    t.category,
    t.visibility,
    v.version,
    v.spec,
    v.label_a,
    v.label_b,
    v.created_at AS version_created_at
FROM recon_mapping_versions v
JOIN recon_mapping_templates t USING (template_id)
WHERE t.archived_at IS NULL
ORDER BY v.template_id, v.version DESC;


-- ============================================================================================================
-- 2. FIFO PRODUCTS & PERIOD CLOSURES
-- ============================================================================================================

CREATE TABLE fifo_products (
    org_id  uuid NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    alias   smallint NOT NULL,
    code    text,
    name    text NOT NULL,
    PRIMARY KEY (org_id, alias)
);

CREATE TABLE fifo_period_closures (
    closure_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id               uuid NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    fiscal_year          smallint NOT NULL CHECK (fiscal_year BETWEEN 2000 AND 2100),
    period               smallint NOT NULL CHECK (period BETWEEN 1 AND 13),
    ordinal              integer GENERATED ALWAYS AS (fiscal_year * 13 + period) STORED,
    status               text NOT NULL DEFAULT 'closed' CHECK (status IN ('closed', 'reopened')),
    engine_version       text NOT NULL,
    snapshot_schema_ver  smallint NOT NULL,
    run_signature        text NOT NULL,
    master_grid_sha256   text,
    receipts_sha256      text,
    control_counts       jsonb NOT NULL,
    ending_qty           numeric(24, 4) NOT NULL,
    ending_value         numeric(18, 2) NOT NULL,
    review_acknowledged  boolean NOT NULL DEFAULT false,
    closed_by            text NOT NULL,
    closed_at            timestamptz NOT NULL DEFAULT now(),
    reopened_by          text,
    reopened_at          timestamptz,

    CHECK ((status = 'reopened') = (reopened_at IS NOT NULL)),
    -- Unique constraint enforcing multi-tenant composite FK
    CONSTRAINT fifo_period_closures_org_id_closure_id_uq UNIQUE (org_id, closure_id)
);

CREATE UNIQUE INDEX fifo_one_closed_per_period
    ON fifo_period_closures (org_id, fiscal_year, period)
    WHERE status = 'closed';

-- Partial index optimizing the latest closed period lookups
CREATE INDEX idx_fifo_closures_latest
    ON fifo_period_closures (org_id, ordinal DESC)
    WHERE status = 'closed';


-- ============================================================================================================
-- 3. FIFO LAYERS & SUMMARIES
-- ============================================================================================================

CREATE TABLE fifo_layers (
    layer_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id       uuid NOT NULL,
    closure_id   uuid NOT NULL,
    alias        smallint NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('beginning', 'ending')),
    layer_seq    integer NOT NULL CHECK (layer_seq >= 1),
    received     text NOT NULL,
    qty          numeric(24, 4) NOT NULL CHECK (qty >= 0),
    unit_cost    numeric(20, 12) NOT NULL CHECK (unit_cost >= 0),
    total_value  numeric(18, 2) NOT NULL CHECK (total_value >= 0),

    UNIQUE (closure_id, kind, alias, layer_seq),

    -- Composite FK enforcing organization-level isolation across closures and products
    CONSTRAINT fk_fifo_layers_closure
        FOREIGN KEY (org_id, closure_id)
        REFERENCES fifo_period_closures(org_id, closure_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_fifo_layers_product
        FOREIGN KEY (org_id, alias)
        REFERENCES fifo_products(org_id, alias)
);

CREATE INDEX idx_fifo_layers_breakdown
    ON fifo_layers (closure_id, kind, alias);

CREATE TABLE fifo_product_period_summary (
    closure_id     uuid NOT NULL,
    org_id         uuid NOT NULL,
    alias          smallint NOT NULL,
    beginning_qty  numeric(24, 4) NOT NULL,  beginning_value numeric(18, 2) NOT NULL,
    received_qty   numeric(24, 4) NOT NULL,  received_value  numeric(18, 2) NOT NULL,
    used_qty       numeric(24, 4) NOT NULL,  used_value      numeric(18, 2) NOT NULL,
    ending_qty     numeric(24, 4) NOT NULL,  ending_value    numeric(18, 2) NOT NULL,
    detail         jsonb,

    PRIMARY KEY (closure_id, alias),
    CONSTRAINT fk_fifo_summary_closure
        FOREIGN KEY (org_id, closure_id)
        REFERENCES fifo_period_closures(org_id, closure_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_fifo_summary_product
        FOREIGN KEY (org_id, alias)
        REFERENCES fifo_products(org_id, alias)
);

-- Optimized View: Resolves current active ending layers per tenant efficiently
CREATE VIEW fifo_current_layers AS
WITH latest_closures AS (
    SELECT DISTINCT ON (org_id) closure_id, org_id
    FROM fifo_period_closures
    WHERE status = 'closed'
    ORDER BY org_id, ordinal DESC
)
SELECT l.*
FROM fifo_layers l
JOIN latest_closures lc ON l.closure_id = lc.closure_id AND l.org_id = lc.org_id
WHERE l.kind = 'ending';


-- ============================================================================================================
-- 4. INTEGRITY CHECK TRIGGER (DEFERRED COMMIT VALIDATION)
-- ============================================================================================================

CREATE OR REPLACE FUNCTION check_fifo_closure_layer_totals()
RETURNS TRIGGER AS $$
DECLARE
    v_sum_qty   numeric(24, 4);
    v_sum_val   numeric(18, 2);
BEGIN
    -- Aggregate ending layer totals for the committed closure
    SELECT COALESCE(SUM(qty), 0), COALESCE(SUM(total_value), 0)
    INTO v_sum_qty, v_sum_val
    FROM fifo_layers
    WHERE closure_id = NEW.closure_id AND kind = 'ending';

    -- Enforce matching balances against period control summary
    IF v_sum_qty <> NEW.ending_qty OR v_sum_val <> NEW.ending_value THEN
        RAISE EXCEPTION 'FIFO Layer imbalance for closure %: expected Qty %, Value %; got Qty %, Value %',
            NEW.closure_id, NEW.ending_qty, NEW.ending_value, v_sum_qty, v_sum_val;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_check_fifo_closure_layer_totals
AFTER INSERT OR UPDATE ON fifo_period_closures
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION check_fifo_closure_layer_totals();
