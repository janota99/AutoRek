-- Hardening for a hosted Supabase project, and a login role for the app.
--
-- Supabase's default setup grants its public web roles (anon, authenticated) access to every new table in the
-- public schema and exposes them through its Data API. These tables hold accounting data and are meant to be reached
-- only through the app, so take that access away, now and for tables created later. Row-level security (migration 002)
-- would already hide the rows; this removes the access as well, so there are two locks instead of one.
--
-- On a plain PostgreSQL server without those roles, the guarded blocks do nothing.

DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', r);
            EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', r);
            EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM %I', r);
            EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %I', r);
            EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I', r);
            EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM %I', r);
        END IF;
    END LOOP;
END
$$;

-- The role the deployed app connects as. It inherits app_runtime_role (read, insert, the two legal updates, no delete)
-- and is subject to row-level security. It has NO password here, so it cannot log in until you set one by hand in the
-- Supabase SQL editor:   ALTER ROLE app_login PASSWORD '<a long random password>';
-- Keep that password in st.secrets or an environment variable, never in the repository. Over the Supabase pooler the
-- user name to connect with is  app_login.<project-ref>.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_login') THEN
        CREATE ROLE app_login LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT IN ROLE app_runtime_role;
    END IF;
END
$$;
