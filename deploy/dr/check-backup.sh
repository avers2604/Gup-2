#!/usr/bin/env bash
set -euo pipefail

STANZA="${PGBACKREST_STANZA:-bz-get}"

if ! command -v pgbackrest >/dev/null 2>&1; then
  echo "ERROR: pgbackrest is not installed" >&2
  exit 2
fi

# `check` validates repository configuration and WAL archiving without
# modifying application data. `info` prints the available backup sets and is
# useful both for operators and monitoring wrappers.
pgbackrest --stanza="$STANZA" check
pgbackrest --stanza="$STANZA" info
