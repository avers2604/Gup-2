#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="1.30.2"
ARCHIVE="verapdf-greenfield-${VERSION}-installer.zip"
BASE_URL="https://software.verapdf.org/rel/1.30"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cd "$WORK"
curl -fsSLO "$BASE_URL/$ARCHIVE"
grep "  $ARCHIVE$" "$ROOT/deploy/verapdf/SHA256SUMS" | sha256sum -c -
unzip -q "$ARCHIVE"
java -jar "verapdf-greenfield-${VERSION}/verapdf-izpack-installer-${VERSION}.jar" \
  "$ROOT/deploy/verapdf/auto-install.xml"
test -x /opt/verapdf/verapdf
/opt/verapdf/verapdf --version | grep -F "1.30.2"
