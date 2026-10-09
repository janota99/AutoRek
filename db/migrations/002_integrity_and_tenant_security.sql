-- Integrity rules, immutability, and tenant isolation on top of 001.
--
--   * Periods close in sequence; beginning layers must equal the prior period's ending layers.
--   * Only the latest closed period can be reopened, and reopening changes nothing else.
--   * The ending-layer check accepts the engine's tolerances (QTY 0.0001, VALUE 0.01) instead of exact equality.
--   * History is append-only: layers, summaries and mapping versions cannot be updated or deleted; closures can only
--     move from closed to reopened.
--   * Deleting an organization is refused (deactivate it instead).
--   * Row-level security on every tenant table, keyed on the app.org_id setting; views respect it.

-- ---------------------------------------------------------------------------------------------------------
-- Organizations: soft delete, and no cascading deletes anywhere.
-- ---------------------------------------------------------------------------------------------------------
ALTER TABLE organizations ADD COLUMN deactivated_at timestamptz;

ALTER TABLE recon_mapping_templates DROP CONSTRAINT recon_mapping_templates_org_id_fkey;
ALTER TABLE recon_mapping_templates ADD CONSTRAINT recon_mapping_templates_org_id_fkey
    FOREIGN KEY (org_id) REFERENCES organizations(org_id) ON DELETE RESTRICT;

ALTER TABLE fifo_products DROP CONSTRAINT fifo_products_org_id_fkey;
ALTER TABLE fifo_products ADD CONSTRAINT fifo_products_org_id_fkey
    FOREIGN KEY (org_id) REFERENCES organizations(org_id) ON DELETE RESTRICT;

ALTER TABLE fifo_period_closures DROP CONSTRAINT fifo_period_closures_org_id_fkey;
ALTER TABLE fifo_period_closures ADD CONSTRAINT fifo_period_closures_org_id_fkey
    FOREIGN KEY (org_id) REFERENCES organizations(org_id) ON DELETE RESTRICT;

ALTER TABLE recon_mapping_versions DROP CONSTRAINT fk_recon_versions_template;
ALTER TABLE recon_mapping_versions ADD CONSTRAINT fk_recon_versions_template
    FOREIGN KEY (org_id, template_id) REFERENCES recon_mapping_templates(org_id, template_id) ON DELETE RESTRICT;

ALTER TABLE fifo_layers DROP CONSTRAINT fk_fifo_layers_closure;
ALTER TABLE fifo_layers ADD CONSTRAINT fk_fifo_layers_closure
    FOREIGN KEY (org_id, closure_id) REFERENCES fifo_period_closures(org_id, closure_id) ON DELETE RESTRICT;

ALTER TABLE fifo_product_period_summary DROP CONSTRAINT fk_fifo_summary_closure;
ALTER TABLE fifo_product_period_summary ADD CONSTRAINT fk_fifo_summary_closure
    FOREIGN KEY (org_id, closure_id) REFERENCES fifo_period_closures(org_id, closure_id) ON DELETE RESTRICT;

-- ---------------------------------------------------------------------------------------------------------
-- Append-only history.
-- ---------------------------------------------------------------------------------------------------------
CREATE FUNCTION forbid_history_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed: history is append-only', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_recon_versions_append_only BEFORE UPDATE OR DELETE ON recon_mapping_versions
    FOR EACH ROW EXECUTE FUNCTION forbid_history_change();
CREATE TRIGGER trg_fifo_layers_append_only BEFORE UPDATE OR DELETE ON fifo_layers
    FOR EACH ROW EXECUTE FUNCTION forbid_history_change();
CREATE TRIGGER trg_fifo_summary_append_only BEFORE UPDATE OR DELETE ON fifo_product_period_summary
    FOR EACH ROW EXECUTE FUNCTION forbid_history_change();
CREATE TRIGGER trg_fifo_closures_no_delete BEFORE DELETE ON fifo_period_closures
    FOR EACH ROW EXECUTE FUNCTION forbid_history_change();

-- ---------------------------------------------------------------------------------------------------------
-- Sequence: a new closure must be the period right after the latest closed one (P13 rolls into the next
-- year's P1 because ordinal = fiscal_year * 13 + period). The first closure of an organization is the opening seed.
-- ---------------------------------------------------------------------------------------------------------
CREATE FUNCTION check_fifo_closure_sequence() RETURNS trigger AS $$
DECLARE
    v_new integer := NEW.fiscal_year * 13 + NEW.period;  -- the generated ordinal is not set yet in a BEFORE trigger
    v_max integer;
BEGIN
    IF NEW.status <> 'closed' THEN
        RAISE EXCEPTION 'A new closure must have status closed';
    END IF;
    SELECT max(ordinal) INTO v_max FROM fifo_period_closures WHERE org_id = NEW.org_id AND status = 'closed';
    IF v_max IS NOT NULL AND v_new <> v_max + 1 THEN
        RAISE EXCEPTION 'Periods close in sequence: FY% P% is out of order (latest closed ordinal %, expected %)',
            NEW.fiscal_year, NEW.period, v_max, v_max + 1;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_fifo_closure_sequence BEFORE INSERT ON fifo_period_closures
    FOR EACH ROW EXECUTE FUNCTION check_fifo_closure_sequence();

-- ---------------------------------------------------------------------------------------------------------
-- Rollforward: the new closure's beginning layers must equal the prior closed period's ending layers, exactly,
-- by product, layer position, quantity and value. Checked at COMMIT, after the layers have been inserted.
-- ---------------------------------------------------------------------------------------------------------
CREATE FUNCTION check_fifo_closure_rollforward() RETURNS trigger AS $$
DECLARE
    v_prior uuid;
BEGIN
    SELECT closure_id INTO v_prior FROM fifo_period_closures
    WHERE org_id = NEW.org_id AND status = 'closed' AND ordinal < NEW.ordinal
    ORDER BY ordinal DESC LIMIT 1;
    IF v_prior IS NULL THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        (SELECT alias, layer_seq, qty, total_value FROM fifo_layers WHERE closure_id = v_prior AND kind = 'ending'
         EXCEPT
         SELECT alias, layer_seq, qty, total_value FROM fifo_layers WHERE closure_id = NEW.closure_id AND kind = 'beginning')
    ) OR EXISTS (
        (SELECT alias, layer_seq, qty, total_value FROM fifo_layers WHERE closure_id = NEW.closure_id AND kind = 'beginning'
         EXCEPT
         SELECT alias, layer_seq, qty, total_value FROM fifo_layers WHERE closure_id = v_prior AND kind = 'ending')
    ) THEN
        RAISE EXCEPTION 'Rollforward break for closure %: beginning layers do not equal the prior period''s ending layers',
            NEW.closure_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_fifo_closure_rollforward AFTER INSERT ON fifo_period_closures
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_fifo_closure_rollforward();

-- ---------------------------------------------------------------------------------------------------------
-- Ending-layer totals: within the engine's tolerances (user_inputs.py: QTY_TOLERANCE 0.0001, VALUE_TOLERANCE 0.01).
-- ---------------------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION check_fifo_closure_layer_totals() RETURNS trigger AS $$
DECLARE
    v_sum_qty numeric(24, 4);
    v_sum_val numeric(18, 2);
BEGIN
    SELECT COALESCE(SUM(qty), 0), COALESCE(SUM(total_value), 0)
    INTO v_sum_qty, v_sum_val
    FROM fifo_layers
    WHERE closure_id = NEW.closure_id AND kind = 'ending';

    IF ABS(v_sum_qty - NEW.ending_qty) > 0.0001 OR ABS(v_sum_val - NEW.ending_value) > 0.01 THEN
        RAISE EXCEPTION 'FIFO layer imbalance for closure % exceeds tolerance: expected Qty %, Value %; got Qty %, Value %',
            NEW.closure_id, NEW.ending_qty, NEW.ending_value, v_sum_qty, v_sum_val;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------------------------------------
-- Reopen: only a closed period, only the latest one, and nothing else about the row may change.
-- ---------------------------------------------------------------------------------------------------------
CREATE FUNCTION guard_fifo_closure_update() RETURNS trigger AS $$
DECLARE
    v_latest integer;
BEGIN
    IF OLD.status <> 'closed' OR NEW.status <> 'reopened' THEN
        RAISE EXCEPTION 'A closure can only change from closed to reopened';
    END IF;
    IF NEW.reopened_by IS NULL OR btrim(NEW.reopened_by) = '' THEN
        RAISE EXCEPTION 'Reopening must record who reopened the period';
    END IF;
    -- ordinal is generated and not yet recomputed in a BEFORE trigger, so it is left out of the comparison
    IF (to_jsonb(NEW) - 'status' - 'reopened_by' - 'reopened_at' - 'ordinal')
       IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'reopened_by' - 'reopened_at' - 'ordinal') THEN
        RAISE EXCEPTION 'Only the reopen fields of a closure may change';
    END IF;
    SELECT max(ordinal) INTO v_latest FROM fifo_period_closures WHERE org_id = OLD.org_id AND status = 'closed';
    IF OLD.ordinal <> v_latest THEN
        RAISE EXCEPTION 'Only the latest closed period can be reopened (FY% P% is not the latest)', OLD.fiscal_year, OLD.period;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_fifo_closure_reopen_guard BEFORE UPDATE ON fifo_period_closures
    FOR EACH ROW EXECUTE FUNCTION guard_fifo_closure_update();

-- ---------------------------------------------------------------------------------------------------------
-- Row-level security. The app sets  SET LOCAL app.org_id = '<uuid>'  at the start of each transaction;
-- with no setting, no rows are visible and none can be written.
-- ---------------------------------------------------------------------------------------------------------
ALTER TABLE organizations              ENABLE ROW LEVEL SECURITY;
ALTER TABLE recon_mapping_templates    ENABLE ROW LEVEL SECURITY;
ALTER TABLE recon_mapping_versions     ENABLE ROW LEVEL SECURITY;
ALTER TABLE fifo_products              ENABLE ROW LEVEL SECURITY;
ALTER TABLE fifo_period_closures       ENABLE ROW LEVEL SECURITY;
ALTER TABLE fifo_layers                ENABLE ROW LEVEL SECURITY;
ALTER TABLE fifo_product_period_summary ENABLE ROW LEVEL SECURITY;

ALTER TABLE organizations              FORCE ROW LEVEL SECURITY;
ALTER TABLE recon_mapping_templates    FORCE ROW LEVEL SECURITY;
ALTER TABLE recon_mapping_versions     FORCE ROW LEVEL SECURITY;
ALTER TABLE fifo_products              FORCE ROW LEVEL SECURITY;
ALTER TABLE fifo_period_closures       FORCE ROW LEVEL SECURITY;
ALTER TABLE fifo_layers                FORCE ROW LEVEL SECURITY;
ALTER TABLE fifo_product_period_summary FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON organizations
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
CREATE POLICY tenant_isolation ON recon_mapping_templates
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
CREATE POLICY tenant_isolation ON recon_mapping_versions
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
CREATE POLICY tenant_isolation ON fifo_products
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
CREATE POLICY tenant_isolation ON fifo_period_closures
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
CREATE POLICY tenant_isolation ON fifo_layers
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
CREATE POLICY tenant_isolation ON fifo_product_period_summary
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);

-- Views run with the caller's rights, so the policies above apply through them.
ALTER VIEW recon_mapping_latest SET (security_invoker = true);
ALTER VIEW fifo_current_layers  SET (security_invoker = true);

-- ---------------------------------------------------------------------------------------------------------
-- The runtime role: read and insert, update only the two tables that have a legal update, never delete.
-- ---------------------------------------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime_role') THEN
        CREATE ROLE app_runtime_role NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO app_runtime_role;
GRANT SELECT, INSERT ON organizations, recon_mapping_templates, recon_mapping_versions, fifo_products,
    fifo_period_closures, fifo_layers, fifo_product_period_summary TO app_runtime_role;
GRANT UPDATE ON recon_mapping_templates, fifo_period_closures TO app_runtime_role;  -- archive a template; reopen a period
GRANT SELECT ON recon_mapping_latest, fifo_current_layers TO app_runtime_role;
GRANT USAGE ON SEQUENCE fifo_layers_layer_id_seq TO app_runtime_role;

REVOKE DELETE ON organizations, recon_mapping_templates, recon_mapping_versions, fifo_products,
    fifo_period_closures, fifo_layers, fifo_product_period_summary FROM app_runtime_role;
REVOKE UPDATE ON recon_mapping_versions, fifo_layers, fifo_product_period_summary FROM app_runtime_role;
