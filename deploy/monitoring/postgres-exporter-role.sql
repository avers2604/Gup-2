-- Run as a PostgreSQL superuser once per cluster.
-- The exporter role is read-only and receives the predefined pg_monitor role.
-- Replace CHANGE_ME_POSTGRES_EXPORTER_PASSWORD before execution and do not
-- commit the resulting credential anywhere.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres_exporter') THEN
        CREATE ROLE postgres_exporter LOGIN;
    END IF;
END
$$;

ALTER ROLE postgres_exporter
    WITH NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;

GRANT pg_monitor TO postgres_exporter;
ALTER ROLE postgres_exporter PASSWORD 'CHANGE_ME_POSTGRES_EXPORTER_PASSWORD';

-- Allow the exporter to connect to the application database. pg_monitor gives
-- access to the statistics views used by postgres_exporter; it does not grant
-- write access to application tables.
GRANT CONNECT ON DATABASE bz_get TO postgres_exporter;
