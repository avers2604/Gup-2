#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

patroni --validate-config --ignore-listen-port "$PATRONI_CONFIG"
haproxy -c -f "$HAPROXY_CONFIG"

# Runtime API contract: admin is root-only; the guard receives a separate
# read-only Unix socket. A TCP stats/runtime listener would violate the local
# least-privilege design.
grep -Eq 'stats socket /run/haproxy/admin\.sock mode 600 level admin' "$HAPROXY_CONFIG" || {
  echo "ERROR: HAProxy admin Runtime API must be mode 600 level admin" >&2; exit 2;
}
grep -Eq 'stats socket /run/haproxy/guard\.sock .*group pgbouncer .*mode 660 level user' "$HAPROXY_CONFIG" || {
  echo "ERROR: HAProxy guard Runtime API must be /run/haproxy/guard.sock group pgbouncer mode 660 level user" >&2; exit 2;
}
if grep -Eq 'stats socket (tcp|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+|0\.0\.0\.0|\*):' "$HAPROXY_CONFIG"; then
  echo "ERROR: network-exposed HAProxy Runtime API is forbidden" >&2
  exit 2
fi

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
        raise SystemExit(f"ERROR: {path}: {key}={actual!r}, expected {expected!r}")

entry = config["databases"].get("bz_get", "")
if "host=127.0.0.1" not in entry or "port=6433" not in entry:
    raise SystemExit("ERROR: bz_get must route PgBouncer to local HAProxy 127.0.0.1:6433")

socket_dir = pool.get("unix_socket_dir", "")
if socket_dir != "/var/run/postgresql":
    raise SystemExit(
        f"ERROR: {path}: unix_socket_dir={socket_dir!r}; primary guard expects /var/run/postgresql"
    )

print("PgBouncer project contract: OK")
PY

python3 -m py_compile "$SCRIPT_DIR/pgbouncer_primary_guard.py"
python3 "$SCRIPT_DIR/test_pgbouncer_primary_guard.py"
grep -q 'RECONNECT' "$SCRIPT_DIR/pgbouncer_primary_guard.py"
grep -q 'WAIT_CLOSE' "$SCRIPT_DIR/pgbouncer_primary_guard.py"
grep -q '/run/haproxy/guard.sock' "$SCRIPT_DIR/pgbouncer_primary_guard.py"

# Имя секции HAProxy, обслуживающей запись, должно совпадать с тем, что ищет
# guard в `show stat`. Рассинхрон здесь не даёт ошибки: guard просто не найдёт
# строк для неизвестного proxy, будет считать топологию неоднозначной и НИ РАЗУ
# не выполнит RECONNECT — защита от Ф-4 молча выключится.
GUARD_PROXY_NAME="${PGB_PRIMARY_GUARD_PROXY_NAME:-$(
  python3 - "$SCRIPT_DIR/pgbouncer_primary_guard.py" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'^DEFAULT_PROXY_NAME\s*=\s*"([^"]+)"', text, re.MULTILINE)
print(match.group(1) if match else "")
PY
)}"
if [[ -z "$GUARD_PROXY_NAME" ]]; then
  echo "ERROR: cannot determine guard proxy name from pgbouncer_primary_guard.py" >&2
  exit 2
fi
if ! grep -Eq "^[[:space:]]*(listen|backend)[[:space:]]+${GUARD_PROXY_NAME}[[:space:]]*$" "$HAPROXY_CONFIG"; then
  echo "ERROR: $HAPROXY_CONFIG has no 'listen/backend ${GUARD_PROXY_NAME}' section;" >&2
  echo "       PgBouncer primary guard would never issue RECONNECT." >&2
  echo "       Rename the section or set PGB_PRIMARY_GUARD_PROXY_NAME." >&2
  exit 2
fi
echo "HAProxy write section matches guard proxy name: ${GUARD_PROXY_NAME}"

echo "HA configuration validation: OK"
