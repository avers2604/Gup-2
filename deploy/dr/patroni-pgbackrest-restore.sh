#!/usr/bin/env bash
# Patroni custom replica creation method backed by pgBackRest.
#
# Patroni passes --scope, --datadir, --role and --connstring. The method is
# intentionally fail-closed until a manual PITR has been approved on the real
# HA/DR stand. When disabled Patroni may continue to the next configured
# create_replica_method (basebackup).
set -euo pipefail
umask 077

PGBACKREST_BIN="${PGBACKREST_BIN:-pgbackrest}"
APPROVAL_FILE="${PATRONI_PGBACKREST_APPROVAL_FILE:-/etc/bz-get/dr/manual-pitr-approved}"
ALLOWED_DATA_ROOT="${PATRONI_ALLOWED_DATA_ROOT:-/var/lib/postgresql}"
EXPECTED_SCOPE="${PATRONI_EXPECTED_SCOPE:-bz-get}"

scope=""
datadir=""
role=""
connstring=""

for arg in "$@"; do
  case "$arg" in
    --scope=*) scope="${arg#*=}" ;;
    --datadir=*) datadir="${arg#*=}" ;;
    --role=*) role="${arg#*=}" ;;
    --connstring=*) connstring="${arg#*=}" ;;
    --*) printf 'INFO: ignoring Patroni argument %s\n' "$arg" >&2 ;;
    *) printf 'ERROR: unexpected positional argument: %s\n' "$arg" >&2; exit 64 ;;
  esac
done

if [[ -z "$scope" || -z "$datadir" ]]; then
  echo "ERROR: Patroni must pass --scope and --datadir" >&2
  exit 64
fi

if [[ "$scope" != "$EXPECTED_SCOPE" ]]; then
  echo "ERROR: scope '$scope' does not match expected '$EXPECTED_SCOPE'" >&2
  exit 65
fi

if [[ -n "$role" && "$role" != "replica" ]]; then
  echo "ERROR: pgBackRest custom creation is allowed only for role=replica" >&2
  exit 65
fi

case "$datadir" in
  "$ALLOWED_DATA_ROOT"/*) ;;
  *) echo "ERROR: datadir '$datadir' is outside allowed root '$ALLOWED_DATA_ROOT'" >&2; exit 65 ;;
esac

if [[ ! -f "$APPROVAL_FILE" ]]; then
  echo "ERROR: pgBackRest Patroni rebuild is disabled." >&2
  echo "Missing approval marker: $APPROVAL_FILE" >&2
  echo "Create it only after the manual PITR/restore drill is accepted." >&2
  exit 78
fi

if [[ -f "$datadir/postmaster.pid" ]]; then
  pid="$(head -n 1 "$datadir/postmaster.pid" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    echo "ERROR: PostgreSQL appears to be running in $datadir (pid=$pid)" >&2
    exit 73
  fi
fi

command -v "$PGBACKREST_BIN" >/dev/null 2>&1 || {
  echo "ERROR: pgbackrest binary not found: $PGBACKREST_BIN" >&2
  exit 69
}
command -v python3 >/dev/null 2>&1 || {
  echo "ERROR: python3 is required for pgBackRest metadata validation" >&2
  exit 69
}

info_json="$("$PGBACKREST_BIN" --stanza="$scope" info --output=json)"
python3 -c '
import json, sys
payload = json.load(sys.stdin)
if not isinstance(payload, list) or not payload:
    raise SystemExit("pgBackRest info returned no stanza")
stanza = payload[0]
status = stanza.get("status", {})
if status.get("code") not in (0, None):
    raise SystemExit(f"pgBackRest stanza is not healthy: {status}")
if not stanza.get("backup"):
    raise SystemExit("pgBackRest repository contains no backup sets")
' <<<"$info_json"

printf 'INFO: restoring Patroni replica scope=%s datadir=%s via pgBackRest\n' "$scope" "$datadir" >&2
if [[ -z "$connstring" ]]; then
  echo "INFO: Patroni supplied no leader connstring; restore will rely on repository/WAL archive." >&2
fi

# Do not set PITR target or target-action=promote here. This path creates a
# replica, not a DR primary. Patroni will configure/start it as a standby after
# the custom creation method succeeds.
exec "$PGBACKREST_BIN" \
  --stanza="$scope" \
  --pg1-path="$datadir" \
  --delta \
  --log-level-console=info \
  restore
