#!/usr/bin/env bash
# Enable Patroni pgBackRest replica rebuild only after a signed PASS drill.
set -euo pipefail
umask 077

DRILL_EVIDENCE_ROOT="${DRILL_EVIDENCE_ROOT:-/var/lib/bz-get/drills}"
APPROVAL_FILE="${PATRONI_PGBACKREST_APPROVAL_FILE:-/etc/bz-get/dr/manual-pitr-approved}"
APPROVAL_GROUP="${PATRONI_PGBACKREST_APPROVAL_GROUP:-postgres}"

usage() {
  cat <<'EOF'
Usage: approve-pgbackrest-rebuild.sh DRILL_ID APPROVER [--execute]

The drill RESULT.md must contain exactly: PASS/FAIL: PASS
Without --execute this command only validates and prints the intended action.
EOF
}

drill_id="${1:-}"
approver="${2:-}"
mode="${3:-}"
[[ "$drill_id" =~ ^[A-Za-z0-9._-]+$ ]] || { usage >&2; exit 64; }
[[ -n "$approver" ]] || { usage >&2; exit 64; }
[[ -z "$mode" || "$mode" == "--execute" ]] || { usage >&2; exit 64; }

result="$DRILL_EVIDENCE_ROOT/$drill_id/RESULT.md"
[[ -r "$result" ]] || { echo "ERROR: drill result not readable: $result" >&2; exit 66; }
grep -Fxq 'PASS/FAIL: PASS' "$result" || {
  echo "ERROR: drill is not explicitly signed PASS in $result" >&2
  exit 78
}

if [[ "$mode" != "--execute" ]]; then
  echo "DRY-RUN: drill $drill_id is signed PASS; approval marker would be written to $APPROVAL_FILE"
  exit 0
fi

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: --execute must run as root to create a root-owned approval marker" >&2
  exit 77
fi
getent group "$APPROVAL_GROUP" >/dev/null || {
  echo "ERROR: required group does not exist: $APPROVAL_GROUP" >&2
  exit 65
}

install -d -o root -g "$APPROVAL_GROUP" -m 0750 "$(dirname "$APPROVAL_FILE")"
tmp="$(mktemp "$(dirname "$APPROVAL_FILE")/.approval.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<EOF
status=approved
manual_pitr_drill_id=$drill_id
approved_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
approved_by=$approver
EOF
chown root:"$APPROVAL_GROUP" "$tmp"
chmod 0640 "$tmp"
mv -f "$tmp" "$APPROVAL_FILE"
trap - EXIT

echo "APPROVED: Patroni pgBackRest rebuild enabled by drill $drill_id; marker=$APPROVAL_FILE"
