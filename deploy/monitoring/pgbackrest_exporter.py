#!/usr/bin/env python3
"""Small Prometheus exporter for pgBackRest backup freshness.

It intentionally uses only the Python standard library and the stable
`pgbackrest info --output=json` interface. The exporter is read-only: it never
starts backup/expire/restore operations.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

STANZA = os.environ.get("PGBACKREST_STANZA", "bz-get")
CONFIG = os.environ.get("PGBACKREST_CONFIG", "/etc/pgbackrest/pgbackrest.conf")
LISTEN_HOST = os.environ.get("PGBACKREST_EXPORTER_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("PGBACKREST_EXPORTER_PORT", "9854"))
CACHE_SECONDS = int(os.environ.get("PGBACKREST_EXPORTER_CACHE_SECONDS", "60"))
COMMAND_TIMEOUT = int(os.environ.get("PGBACKREST_EXPORTER_TIMEOUT_SECONDS", "20"))

_lock = threading.Lock()
_cache_at = 0.0
_cache_text = ""


def _metric_label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _backup_type(item: dict[str, Any]) -> str:
    value = str(item.get("type", "")).lower()
    if value in {"full", "diff", "incr"}:
        return value
    label = str(item.get("label", ""))
    suffix = label[-1:].upper()
    return {"F": "full", "D": "diff", "I": "incr"}.get(suffix, "unknown")


def _stanza_ok(value: Any) -> bool:
    if isinstance(value, dict):
        code = value.get("code")
        message = str(value.get("message", "")).lower()
        return code in (0, "0", None) and message in ("", "ok")
    return str(value).lower() == "ok"


def _collect() -> str:
    now = time.time()
    command = [
        "pgbackrest",
        f"--config={CONFIG}",
        f"--stanza={STANZA}",
        "--output=json",
        "info",
    ]
    lines = [
        "# HELP pgbackrest_exporter_up 1 when pgbackrest info succeeded and JSON was parsed.",
        "# TYPE pgbackrest_exporter_up gauge",
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
        payload = json.loads(completed.stdout)
        stanza = payload[0] if payload else {}
        backups = stanza.get("backup") or []
        archives = stanza.get("archive") or []

        lines.append("pgbackrest_exporter_up 1")
        lines.extend(
            [
                "# HELP pgbackrest_stanza_ok 1 when pgBackRest reports stanza status ok.",
                "# TYPE pgbackrest_stanza_ok gauge",
                f'pgbackrest_stanza_ok{{stanza="{_metric_label(STANZA)}"}} {1 if _stanza_ok(stanza.get("status")) else 0}',
                "# HELP pgbackrest_backup_count Number of known backups by type.",
                "# TYPE pgbackrest_backup_count gauge",
                "# HELP pgbackrest_last_backup_timestamp_seconds Stop time of the latest backup by type.",
                "# TYPE pgbackrest_last_backup_timestamp_seconds gauge",
                "# HELP pgbackrest_last_backup_age_seconds Age of the latest backup by type.",
                "# TYPE pgbackrest_last_backup_age_seconds gauge",
                "# HELP pgbackrest_archive_present 1 when pgBackRest reports at least one archived WAL range.",
                "# TYPE pgbackrest_archive_present gauge",
            ]
        )

        by_type: dict[str, list[dict[str, Any]]] = {}
        for backup in backups:
            by_type.setdefault(_backup_type(backup), []).append(backup)

        for backup_type in ("full", "diff", "incr", "unknown"):
            items = by_type.get(backup_type, [])
            lines.append(
                f'pgbackrest_backup_count{{stanza="{_metric_label(STANZA)}",type="{backup_type}"}} {len(items)}'
            )
            if items:
                stop = float(items[-1].get("timestamp", {}).get("stop", 0) or 0)
                if stop > 0:
                    lines.append(
                        f'pgbackrest_last_backup_timestamp_seconds{{stanza="{_metric_label(STANZA)}",type="{backup_type}"}} {stop:.0f}'
                    )
                    lines.append(
                        f'pgbackrest_last_backup_age_seconds{{stanza="{_metric_label(STANZA)}",type="{backup_type}"}} {max(0.0, now - stop):.0f}'
                    )

        if backups:
            stop = float(backups[-1].get("timestamp", {}).get("stop", 0) or 0)
            if stop > 0:
                lines.append(
                    f'pgbackrest_last_backup_timestamp_seconds{{stanza="{_metric_label(STANZA)}",type="any"}} {stop:.0f}'
                )
                lines.append(
                    f'pgbackrest_last_backup_age_seconds{{stanza="{_metric_label(STANZA)}",type="any"}} {max(0.0, now - stop):.0f}'
                )
        lines.append(
            f'pgbackrest_archive_present{{stanza="{_metric_label(STANZA)}"}} {1 if archives and archives[-1].get("max") else 0}'
        )
        lines.extend(
            [
                "# HELP pgbackrest_exporter_last_refresh_timestamp_seconds Time of the last successful pgBackRest info collection.",
                "# TYPE pgbackrest_exporter_last_refresh_timestamp_seconds gauge",
                f"pgbackrest_exporter_last_refresh_timestamp_seconds {now:.0f}",
            ]
        )
    except Exception:
        # Prometheus must still get a valid response so `exporter_up=0` can alert.
        lines.append("pgbackrest_exporter_up 0")
    return "\n".join(lines) + "\n"


def metrics() -> str:
    global _cache_at, _cache_text
    now = time.monotonic()
    with _lock:
        if _cache_text and now - _cache_at < CACHE_SECONDS:
            return _cache_text
        _cache_text = _collect()
        _cache_at = now
        return _cache_text


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/-/healthy":
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/metrics":
            self.send_error(404)
            return
        body = metrics().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
