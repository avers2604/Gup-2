from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    path = ROOT / relative
    return path.read_text(encoding="utf-8") if path.exists() else ""


class InfrastructureHardeningContracts(unittest.TestCase):
    def test_postgres_compose_separates_bootstrap_runtime_and_grafana_credentials(self):
        compose = read("docker-compose.yml")
        self.assertIn("POSTGRES_USER: ${POSTGRES_ADMIN_USER:", compose)
        self.assertIn("POSTGRES_PASSWORD: ${POSTGRES_ADMIN_PASSWORD:", compose)
        self.assertIn("DB_USER: ${POSTGRES_USER:", compose)
        self.assertIn("DB_PASSWORD: ${POSTGRES_PASSWORD:", compose)
        self.assertIn("GRAFANA_POSTGRES_USER: ${GRAFANA_POSTGRES_USER:", compose)
        self.assertIn("GRAFANA_POSTGRES_PASSWORD: ${GRAFANA_POSTGRES_PASSWORD:", compose)

    def test_postgres_bootstrap_makes_migrator_owner_and_runtime_non_owner(self):
        bootstrap = read("deploy/postgres/bootstrap-roles.sql")
        self.assertTrue(bootstrap, "missing PostgreSQL role bootstrap")
        self.assertIn('ALTER DATABASE :"db_name" OWNER TO :"migration_user";', bootstrap)
        self.assertIn('ALTER SCHEMA public OWNER TO :"migration_user";', bootstrap)
        self.assertIn("REVOKE CREATE ON SCHEMA public FROM PUBLIC;", bootstrap)
        self.assertIn('ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user"', bootstrap)
        self.assertNotIn('OWNER TO :"runtime_user"', bootstrap)

    def test_post_migrate_grants_preserve_worm_and_limit_grafana(self):
        grants = read("deploy/postgres/post-migrate-grants.sql")
        self.assertTrue(grants, "missing post-migrate least-privilege grants")
        self.assertIn(
            'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_auditlog FROM :"runtime_user";',
            grants,
        )
        self.assertIn('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM :"grafana_user";', grants)
        self.assertIn('GRANT SELECT ON TABLE audit_auditlog TO :"grafana_user";', grants)
        self.assertNotIn('GRANT ALL', grants.upper())

    def test_migration_helper_uses_migration_credentials_then_reapplies_grants(self):
        script = read("deploy/postgres/migrate.sh")
        self.assertTrue(script, "missing migration-role helper")
        self.assertIn('POSTGRES_USER="$POSTGRES_MIGRATION_USER"', script)
        self.assertIn('POSTGRES_PASSWORD="$POSTGRES_MIGRATION_PASSWORD"', script)
        self.assertIn("python manage.py migrate", script)
        self.assertIn("post-migrate-grants.sql", script)

    def test_grafana_datasource_uses_dedicated_readonly_role(self):
        datasource = read("deploy/grafana/provisioning/datasources/postgres.yml")
        self.assertIn("user: $GRAFANA_POSTGRES_USER", datasource)
        self.assertIn("password: $GRAFANA_POSTGRES_PASSWORD", datasource)
        self.assertNotIn("user: $POSTGRES_USER", datasource)
        self.assertNotIn("password: $POSTGRES_PASSWORD", datasource)

    def test_minio_bootstrap_and_application_credentials_are_distinct(self):
        compose = read("docker-compose.yml")
        init_script = read("deploy/minio/init-bucket.sh")
        self.assertIn("MINIO_ROOT_USER: ${MINIO_ROOT_USER:", compose)
        self.assertIn("MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:", compose)
        self.assertIn("MINIO_ACCESS_KEY: ${MINIO_ACCESS_KEY:", compose)
        self.assertIn("MINIO_SECRET_KEY: ${MINIO_SECRET_KEY:", compose)
        self.assertIn('mc admin user add local "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"', init_script)
        self.assertIn("mc admin policy attach", init_script)

    def test_minio_originals_policy_cannot_delete_or_bypass_retention(self):
        raw = read("deploy/minio/app-policy.json.tpl")
        self.assertTrue(raw, "missing application MinIO policy template")
        policy = json.loads(
            raw.replace("__ORIGINALS_BUCKET__", "originals").replace(
                "__WORKING_BUCKET__", "working"
            )
        )
        originals_actions: set[str] = set()
        for statement in policy.get("Statement", []):
            resources = statement.get("Resource", [])
            if isinstance(resources, str):
                resources = [resources]
            if any("originals" in resource for resource in resources):
                actions = statement.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                originals_actions.update(actions)

        self.assertIn("s3:PutObject", originals_actions)
        self.assertIn("s3:GetObject", originals_actions)
        self.assertNotIn("s3:DeleteObject", originals_actions)
        self.assertNotIn("s3:DeleteObjectVersion", originals_actions)
        self.assertNotIn("s3:BypassGovernanceRetention", originals_actions)


if __name__ == "__main__":
    unittest.main()
