#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERAPDF="${1:-/opt/verapdf/verapdf}"
PASS="$ROOT/apps/core/tests/fixtures/pdfa-2b-valid.pdf"
FAIL="$ROOT/apps/core/tests/fixtures/pdfa-2b-invalid.pdf"

"$VERAPDF" --version | grep -F "1.30.2"
"$VERAPDF" -l | grep -F "2b"
pdfinfo "$PASS" >/dev/null
pdfinfo "$FAIL" >/dev/null
"$VERAPDF" -f 2b "$PASS"
set +e
"$VERAPDF" -f 2b "$FAIL"
rc=$?
set -e
[[ "$rc" -eq 1 ]]
