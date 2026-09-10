#!/usr/bin/env bash
set -euo pipefail

PATRONI_CONFIG="${PATRONI_CONFIG:-/etc/patroni/patroni.yml}"
HAPROXY_CONFIG="${HAPROXY_CONFIG:-/etc/haproxy/haproxy.cfg}"
PGBOUNCER_CONFIG="${PGBOUNCER_CONFIG:-/etc/pgbouncer/pgbouncer.ini}"

for file in "$PATRONI_CONFIG" "$HAPROXY_CONFIG" "$PGBOUNCER_CONFIG"; do
  if [[ ! -r "$file" ]]; then
    echo "ERROR: config is not readable: $file" >&2
    exit 2
  fi
  if grep -q 'CHANGE_ME' "$file"; then
    echo "ERROR: unresolved CHANGE_ME placeholder in $file" >&2
    exit 2
  fi
done

for command in patroni haproxy python3; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "ERROR: required command not installed: $command" >&2
    exit 2
  fi
done

# Patroni 4.1+ validates schema/GUCs. Ignore bound ports because this script is
# intended to be safe to run on an already provisioned node.
patroni --validate-config --ignore-listen-port "$PATRONI_CONFIG"

# HAProxy performs a full syntax/configuration validation without starting.
haproxy -c -f "$HAPROXY_CONFIG"

# PgBouncer has no equally useful no-start validator, so parse the INI and
# assert the contract this project relies on.
python3 - "$PGBOUNCER_CONFIG" <<'PY'
import configparser
import sys

path = sys.argv[1]
config = configparser.ConfigParser(interpolation=None)
with open(path, encoding="utf-8") as stream:
    config.read_file(stream)

for section in ("databases", "pgbouncer"):
    if section not in config:
        raise SystemExit(f"ERROR: missing [{section}] in {path}")

pool = config["pgbouncer"]
required = {
    "listen_addr": "127.0.0.1",
    "listen_port": "6432",
    "pool_mode": "transaction",
    "auth_type": "scram-sha-256",
}
for key, expected in required.items():
    actual = pool.get(key)
    if actual != expected:
        raise SystemExit(
            f"ERROR: {path}: {key}={actual!r}, expected {expected!r}"
        )

entry = config["databases"].get("bz_get", "")
if "host=127.0.0.1" not in entry or "port=6433" not in entry:
    raise SystemExit(
        "ERROR: bz_get must route PgBouncer to local HAProxy 127.0.0.1:6433"
    )

print("PgBouncer project contract: OK")
PY

echo "HA configuration validation: OK"
