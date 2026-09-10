#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# Shell syntax for manual test helper.
bash -n test-alert.sh

# Baseline из README.md этого каталога. Проверка конфигурации имеет смысл только
# против той версии, на которой её будут разворачивать: набор допустимых полей
# между версиями меняется. Проверено на репетиции стенда
# (docs/STAGE4_LAB_REHEARSAL.md): amtool 0.27.0 отвергает валидную для baseline
# конфигурацию с "field timeout not found in type config.plain", потому что
# webhook-опция timeout появилась только в 0.28.0. Оператор с несовпадающим
# amtool получил бы ложный отказ и потерял бы время, разбирая исправный конфиг.
AMTOOL_BASELINE_VERSION="${AMTOOL_BASELINE_VERSION:-0.33.1}"

if ! command -v amtool >/dev/null 2>&1; then
  echo "amtool not installed; shell checks passed, Alertmanager semantic validation skipped."
  echo "WARNING: на приёмочном стенде эта проверка обязана выполняться amtool $AMTOOL_BASELINE_VERSION."
  exit 0
fi

amtool_version="$(amtool --version 2>&1 | head -1 | sed -n 's/.*version \([0-9][0-9.]*\).*/\1/p')"
if [[ -z "$amtool_version" ]]; then
  echo "WARNING: не удалось определить версию amtool; результат проверки не привязан к baseline." >&2
elif [[ "$amtool_version" != "$AMTOOL_BASELINE_VERSION" ]]; then
  echo "WARNING: amtool $amtool_version не совпадает с baseline $AMTOOL_BASELINE_VERSION." >&2
  echo "         Отказ проверки может быть вызван разницей версий, а не конфигурацией." >&2
else
  echo "amtool $amtool_version соответствует baseline."
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
