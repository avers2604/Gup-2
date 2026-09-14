\set ON_ERROR_STOP on

-- Re-apply least privilege after every Django migration. The migrator owns all
-- application objects; the runtime role receives only the DML it needs.
GRANT USAGE ON SCHEMA public TO :"runtime_user", :"grafana_user";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"runtime_user";
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO :"runtime_user";

-- WORM audit rows are append-only for the application role even if an attacker
-- gains arbitrary SQL through the web process. Because runtime is not owner,
-- it also cannot disable the trigger that enforces immutability.
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_auditlog FROM :"runtime_user";

-- Monitoring gets no blanket table privileges: only the WORM audit source used
-- by provisioned Grafana alerts is readable.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM :"grafana_user";
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM :"grafana_user";
GRANT SELECT ON TABLE audit_auditlog TO :"grafana_user";
