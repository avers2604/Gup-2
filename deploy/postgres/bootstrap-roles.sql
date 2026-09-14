\set ON_ERROR_STOP on

-- Run as the PostgreSQL bootstrap/admin role. The migration role owns the
-- database/schema and therefore all Django-created objects; the long-running
-- application role is deliberately not an owner and cannot disable WORM
-- triggers or replace trigger functions.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'migration_user', :'migration_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migration_user')
\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'runtime_user', :'runtime_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'runtime_user')
\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'grafana_user', :'grafana_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'grafana_user')
\gexec

ALTER ROLE :"migration_user" PASSWORD :'migration_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE :"runtime_user" PASSWORD :'runtime_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE :"grafana_user" PASSWORD :'grafana_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

ALTER DATABASE :"db_name" OWNER TO :"migration_user";
REVOKE CONNECT ON DATABASE :"db_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db_name" TO :"migration_user", :"runtime_user", :"grafana_user";

ALTER SCHEMA public OWNER TO :"migration_user";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"runtime_user", :"grafana_user";

-- Objects created by future Django migrations are immediately usable by the
-- runtime role, but ownership stays with the migration role. WORM-specific
-- mutation privileges are removed again by post-migrate-grants.sql.
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO :"runtime_user";
