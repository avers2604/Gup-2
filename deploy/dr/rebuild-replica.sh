#!/usr/bin/env bash
# Safely request rebuild of one Patroni replica.
# Default mode is dry-run. Pass --execute only after reviewing preflight output.
set -euo pipefail
umask 077

PATRONI_CONFIG="${PATRONI_CONFIG:-/etc/patroni/patroni.yml}"
PATRONI_SCOPE="${PATRONI_SCOPE:-bz-get}"
APPROVAL_FILE="${PATRONI_PGBACKREST_APPROVAL_FILE:-/etc/bz-get/dr/manual-pitr-approved}"
PGBACKREST_BIN="${PGBACKREST_BIN:-pgbackrest}"

usage() {
  cat <<'EOF'
Usage: rebuild-replica.sh MEMBER [--execute]

Without --execute the command performs preflight checks and prints the exact
patronictl reinit command that would be run.
EOF
}

member="${1:-}"
mode="${2:-}"
if [[ -z "$member" || "$member" == -* ]]; then
  usage >&2
  exit 64
fi
if [[ -n "$mode" && "$mode" != "--execute" ]]; then
  usage >&2
  exit 64
fi

for cmd in patronictl python3 "$PGBACKREST_BIN"; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $cmd" >&2
    exit 69
  }
done
[[ -r "$PATRONI_CONFIG" ]] || { echo "ERROR: cannot read $PATRONI_CONFIG" >&2; exit 66; }
[[ -f "$APPROVAL_FILE" ]] || {
  echo "ERROR: pgBackRest rebuild is not approved; missing $APPROVAL_FILE" >&2
  exit 78
}

cluster_json="$(patronictl -c "$PATRONI_CONFIG" list "$PATRONI_SCOPE" --format=json)"
python3 - "$member" <<'PY' <<<"$cluster_json"
import json, sys
member = sys.argv[1]
rows = json.load(sys.stdin)
normalized = []
for row in rows:
    normalized.append({str(k).strip().lower(): v for k, v in row.items()})
selected = next((r for r in normalized if str(r.get("member", "")) == member), None)
if selected is None:
    raise SystemExit(f"member {member!r} not found in Patroni cluster")
role = str(selected.get("role", "")).strip().lower()
state = str(selected.get("state", "")).strip().lower()
if role in {"leader", "primary", "standby leader", "standby-leader"}:
    raise SystemExit(f"refusing to rebuild primary/leader member {member!r} (role={role})")
if "replica" not in role and "standby" not in role:
    raise SystemExit(f"member {member!r} is not a replica (role={role!r}, state={state!r})")
leaders = [r for r in normalized if str(r.get("role", "")).strip().lower() in {"leader", "primary"}]
if len(leaders) != 1:
    raise SystemExit(f"expected exactly one writable leader before replica rebuild, found {len(leaders)}")
print(f"Preflight Patroni: target={member} role={role} state={state}; leader={leaders[0].get('member')}")
PY

info_json="$("$PGBACKREST_BIN" --stanza="$PATRONI_SCOPE" info --output=json)"
python3 - <<'PY' <<<"$info_json"
import json, sys
payload = json.load(sys.stdin)
if not payload:
    raise SystemExit("pgBackRest info returned no stanza")
stanza = payload[0]
if stanza.get("status", {}).get("code") not in (0, None):
    raise SystemExit(f"pgBackRest stanza unhealthy: {stanza.get('status')}")
backups = stanza.get("backup") or []
if not backups:
    raise SystemExit("pgBackRest repository has no backup sets")
latest = backups[-1]
print("Preflight pgBackRest: backup sets=%d latest=%s" % (len(backups), latest.get("label", "unknown")))
PY

cmd=(patronictl -c "$PATRONI_CONFIG" reinit "$PATRONI_SCOPE" "$member" --wait --force)
printf 'Planned command:'
printf ' %q' "${cmd[@]}"
printf '\n'

if [[ "$mode" != "--execute" ]]; then
  echo "DRY-RUN: no destructive action performed. Re-run with --execute after change approval."
  exit 0
fi

start_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "EXECUTE: replica rebuild started at $start_utc"
"${cmd[@]}"

post_json="$(patronictl -c "$PATRONI_CONFIG" list "$PATRONI_SCOPE" --format=json)"
python3 - "$member" <<'PY' <<<"$post_json"
import json, sys
member = sys.argv[1]
rows = [{str(k).strip().lower(): v for k, v in row.items()} for row in json.load(sys.stdin)]
selected = next((r for r in rows if str(r.get("member", "")) == member), None)
if selected is None:
    raise SystemExit(f"rebuilt member {member!r} disappeared from cluster")
role = str(selected.get("role", "")).lower()
state = str(selected.get("state", "")).lower()
if "replica" not in role and "standby" not in role:
    raise SystemExit(f"post-check failed: role={role!r}")
if state not in {"running", "streaming"}:
    raise SystemExit(f"post-check failed: state={state!r}")
print(f"Post-check Patroni: target={member} role={role} state={state}")
PY

end_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "SUCCESS: replica rebuild completed at $end_utc"
