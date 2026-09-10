#!/usr/bin/env bash
# Замер отказоустойчивости С ТОЧКИ ЗРЕНИЯ ПРИЛОЖЕНИЯ.
#
# Зачем отдельный инструмент. Оператор, меряющий RTO подключением psql к
# HAProxy, получает не тот показатель, который переживает АИС: на
# стенде-репетиции (docs/STAGE4_LAB_REHEARSAL.md) прямое подключение к HAProxy
# восстанавливалось меньше чем за секунду, а приложение, ходящее через
# PgBouncer, оставалось приколотым к ДЕМОУТНУТОМУ узлу ещё десятки секунд и всё
# это время получало отказ на любой записи. Разница между двумя цифрами — 1 с
# против 55 с — и есть цена измерения не на том слое.
#
# Проба различает ТРИ состояния, а не два:
#   OK        — соединение есть, узел доступен для записи;
#   READ_ONLY — соединение есть, но узел в recovery: SELECT проходит, INSERT нет;
#   FAIL      — соединения нет.
#
# READ_ONLY опаснее FAIL: приложение получает не отказ соединения, а успешную
# сессию, в которой падает только запись. Мониторинг, проверяющий «база
# отвечает», такое окно не увидит.
#
# Использование:
#   write-path-probe.sh --dsn-port 6432 --user bz_get --db bz_get \
#     --duration 120 --out /var/lib/bz-get/acceptance/RUN/write-path.csv
#
# Пароль берётся из PGPASSWORD; в файл evidence он не попадает.
# Формат CSV: timestamp_utc,state,server_port,in_recovery
set -euo pipefail
umask 077

host=127.0.0.1
port=6432
user=bz_get
db=bz_get
duration=120
interval=0.4
out=""

usage() {
  cat <<'EOF'
Usage: write-path-probe.sh [options]
  --host HOST        адрес точки входа приложения (по умолчанию 127.0.0.1)
  --dsn-port PORT    порт PgBouncer, к которому ходит приложение (6432)
  --user USER        пользователь БД (bz_get)
  --db NAME          база (bz_get)
  --duration SEC     длительность наблюдения (120)
  --interval SEC     шаг опроса (0.4)
  --out FILE         CSV-файл evidence (обязателен)
EOF
}

while (( $# > 0 )); do
  case "$1" in
    --host) host="$2"; shift 2 ;;
    --dsn-port) port="$2"; shift 2 ;;
    --user) user="$2"; shift 2 ;;
    --db) db="$2"; shift 2 ;;
    --duration) duration="$2"; shift 2 ;;
    --interval) interval="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done

[[ -n "$out" ]] || { echo "ERROR: --out is required" >&2; exit 64; }
[[ "$duration" =~ ^[0-9]+$ ]] || { echo "ERROR: --duration must be an integer" >&2; exit 64; }
command -v psql >/dev/null 2>&1 || { echo "ERROR: psql is required" >&2; exit 69; }

printf 'timestamp_utc,state,server_port,in_recovery\n' >"$out"

end=$(( $(date +%s) + duration ))
while [[ $(date +%s) -lt $end ]]; do
  ts="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
  if row="$(timeout 5 psql -h "$host" -p "$port" -U "$user" -d "$db" -Atc \
      "select inet_server_port()||','||pg_is_in_recovery()" 2>/dev/null)"; then
    server_port="${row%%,*}"
    in_recovery="${row##*,}"
    if [[ "$in_recovery" == "t" || "$in_recovery" == "true" ]]; then
      printf '%s,READ_ONLY,%s,%s\n' "$ts" "$server_port" "$in_recovery" >>"$out"
    else
      printf '%s,OK,%s,%s\n' "$ts" "$server_port" "$in_recovery" >>"$out"
    fi
  else
    printf '%s,FAIL,,\n' "$ts" >>"$out"
  fi
  sleep "$interval"
done

python3 - "$out" <<'PY'
import sys
from datetime import datetime
from pathlib import Path

rows = [line.split(",") for line in Path(sys.argv[1]).read_text().splitlines()[1:] if line]
if not rows:
    raise SystemExit("write-path probe collected no samples")


def ts(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")


def window(states):
    hit = [r for r in rows if r[1] in states]
    if not hit:
        return 0.0, 0
    return (ts(hit[-1][0]) - ts(hit[0][0])).total_seconds(), len(hit)


fail_span, fail_n = window({"FAIL"})
ro_span, ro_n = window({"READ_ONLY"})
unusable_span, unusable_n = window({"FAIL", "READ_ONLY"})

print(f"samples: {len(rows)}")
print(f"connection failures: {fail_n} samples, span {fail_span:.1f}s")
print(f"read-only routing:   {ro_n} samples, span {ro_span:.1f}s")
print(f"write unavailable:   {unusable_n} samples, span {unusable_span:.1f}s")
print()
print("ВНИМАНИЕ: для приёмочного RTO значим последний показатель — окно, в")
print("котором приложение НЕ МОГЛО ПИСАТЬ. Окно отказа соединения меньше его и")
print("самостоятельного значения не имеет.")
if ro_n:
    print()
    print("Обнаружена маршрутизация на узел в recovery: приложение получало")
    print("рабочую сессию, в которой падала запись. Это не считается успехом")
    print("переключения — см. docs/STAGE4_LAB_REHEARSAL.md, находка Ф-4.")
PY
