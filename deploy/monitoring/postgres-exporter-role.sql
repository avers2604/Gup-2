-- Run as a PostgreSQL superuser once per cluster.
-- The exporter runs locally under the OS account `postgres_exporter` and uses
-- Unix-socket peer authentication. No database password is required.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres_exporter') THEN
        CREATE ROLE postgres_exporter LOGIN;
    END IF;
END
$$;

ALTER ROLE postgres_exporter
    WITH NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT NOREPLICATION;

-- pg_monitor is the predefined read-only statistics role intended for
-- monitoring. INHERIT is required so the exporter receives its privileges
-- without issuing SET ROLE.
GRANT pg_monitor TO postgres_exporter;
GRANT CONNECT ON DATABASE bz_get TO postgres_exporter;
