# PostgreSQL least-privilege roles

Production uses four separate identities:

- bootstrap/admin: infrastructure only; creates/rotates the other roles;
- migration: owns the database/schema and Django-created objects;
- runtime (`POSTGRES_USER`): long-lived web/worker DML role, never an owner;
- Grafana: `SELECT` only on `audit_auditlog`.

This ownership split is part of the WORM boundary. The audit trigger blocks
`UPDATE`/`DELETE`, but a table owner could disable that trigger. Therefore the
web/worker role must never own the audit table or receive migration/admin
credentials.

## New local/reference database

Copy `deploy/infra.env.example` to the ignored `deploy/infra.env`, replace all
example secrets, then start PostgreSQL with:

```bash
docker compose --env-file deploy/infra.env up -d postgres
```

On the first initialization the mounted `init-bootstrap.sh` runs
`bootstrap-roles.sql`. Existing database volumes are not changed by Docker's
init hook.

## Existing database / production

Run `bootstrap-roles.sql` once as the infrastructure PostgreSQL admin, supplying
the psql variables shown in `init-bootstrap.sh`. Then run migrations only through
`deploy/postgres/migrate.sh` with migration credentials injected from the
infrastructure secret store. The helper applies Django migrations and immediately
re-applies `post-migrate-grants.sql`, including the explicit removal of
`UPDATE`/`DELETE`/`TRUNCATE` on `audit_auditlog` from the runtime role.

For HA production, set `POSTGRES_MIGRATION_HOST` / `POSTGRES_MIGRATION_PORT` to a
controlled writable-primary endpoint. Do not put the migration identity into the
runtime PgBouncer user set.
