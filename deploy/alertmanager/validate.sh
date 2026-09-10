#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# Shell syntax for manual test helper.
bash -n test-alert.sh

if ! command -v amtool >/dev/null 2>&1; then
  echo "amtool not installed; shell checks passed, Alertmanager semantic validation skipped."
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

cp alertmanager.yml.example "$tmp/alertmanager.yml"
printf '%s\n' 'https://warning.receiver.invalid/alerts' > "$tmp/warning-webhook.url"
printf '%s\n' 'https://critical.receiver.invalid/alerts' > "$tmp/critical-webhook.url"

sed -i \
  -e "s#/etc/bz-get/alertmanager/warning-webhook.url#$tmp/warning-webhook.url#g" \
  -e "s#/etc/bz-get/alertmanager/critical-webhook.url#$tmp/critical-webhook.url#g" \
  "$tmp/alertmanager.yml"

amtool check-config "$tmp/alertmanager.yml"
echo "Alertmanager configuration validation passed."
