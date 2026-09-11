#!/usr/bin/env python3
"""Reconnect local PgBouncer when local HAProxy changes PostgreSQL primary.

Topology on every application node:

    Django -> PgBouncer :6432 -> HAProxy :6433 -> Patroni primary

HAProxy correctly changes the backend selected by Patroni `/primary` health
checks, but PgBouncer can keep already-open server connections to the demoted
primary. Those connections remain usable for reads and fail writes with a
read-only error after a planned switchover.

PgBouncer documents RECONNECT as the command intended for exactly this case:
a downstream component such as HAProxy changes its routing while PgBouncer's
own database connection string stays unchanged. WAIT_CLOSE then confirms that
all server connections marked close_needed have actually left the pool.

Trigger model: this guard POLLS HAProxy's dedicated read-only Unix Runtime API
socket (default once per second); it is not a Patroni callback. Therefore the
reaction window is bounded by HAProxy health convergence + <= one poll interval
+ RECONNECT/WAIT_CLOSE. Real acceptance must remeasure that window through
`deploy/acceptance/write-path-probe.sh` on PgBouncer :6432.

The Runtime API is intentionally not exposed over TCP. HAProxy creates a
separate `level user` socket owned for the pgbouncer Unix account; the guard
cannot execute HAProxy administrative commands. PgBouncer administration also
uses the local Unix socket and the built-in same-UID `pgbouncer` console user,
so the process needs no PostgreSQL/application password. Every topology change,
ambiguous topology and RECONNECT result is timestamped in the service journal.
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import socket
import subprocess
import sys
import time

LOG = logging.getLogger("pgbouncer-primary-guard")

DEFAULT_HAPROXY_SOCKET = "/run/haproxy/guard.sock"
# Must match the HAProxy section name serving writes (`listen postgres_primary`
# in haproxy.cfg.example). A mismatch is not a loud failure: `show stat` simply
# reports no rows for the unknown proxy, the guard treats every sample as an
# ambiguous topology and never issues RECONNECT. deploy/ha/validate.sh asserts
# the two stay in sync.
DEFAULT_PROXY_NAME = "postgres_primary"
DEFAULT_PGBOUNCER_SOCKET_DIR = "/var/run/postgresql"
DEFAULT_PGBOUNCER_PORT = 6432
DEFAULT_DATABASE = "bz_get"
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_COMMAND_TIMEOUT = 60.0


class TopologyAmbiguous(RuntimeError):
    pass


def parse_primary_backend(payload: str, *, proxy_name: str = DEFAULT_PROXY_NAME) -> str:
    """Return the single HAProxy server currently UP for the write backend."""
    if not payload.strip():
        raise TopologyAmbiguous("HAProxy returned an empty stats response")

    lines = payload.splitlines()
    if not lines:
        raise TopologyAmbiguous("HAProxy returned no stats rows")
    if lines[0].startswith("# "):
        lines[0] = lines[0][2:]
    elif lines[0].startswith("#"):
        lines[0] = lines[0][1:].lstrip()

    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    up = []
    for row in reader:
        if (row.get("pxname") or "").strip() != proxy_name:
            continue
        server = (row.get("svname") or "").strip()
        if not server or server in {"FRONTEND", "BACKEND"}:
            continue
        status = (row.get("status") or "").strip().upper()
        if status.startswith("UP"):
            up.append(server)

    if len(up) != 1:
        raise TopologyAmbiguous(
            f"expected exactly one UP server in {proxy_name!r}, found {up!r}"
        )
    return up[0]


def haproxy_stats(socket_path: str, *, timeout: float = 2.0) -> str:
    """Read `show stat` from the local read-only HAProxy Runtime API socket."""
    chunks = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(socket_path)
        client.sendall(b"show stat\n")
        client.shutdown(socket.SHUT_WR)
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="strict")


def reconnect_pgbouncer(
    *,
    database: str,
    socket_dir: str,
    port: int,
    timeout: float,
    psql: str = "psql",
) -> None:
    """Force existing server connections through HAProxy's current route."""
    command = [
        psql,
        "-X",
        "-q",
        "-w",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        socket_dir,
        "-p",
        str(port),
        "-U",
        "pgbouncer",
        "-d",
        "pgbouncer",
        "-c",
        f"RECONNECT {database};",
        "-c",
        f"WAIT_CLOSE {database};",
    ]
    subprocess.run(
        command,
        check=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        text=True,
    )


def observe_once(
    *,
    previous_backend: str | None,
    haproxy_socket: str,
    database: str,
    pgbouncer_socket_dir: str,
    pgbouncer_port: int,
    command_timeout: float,
    psql: str,
    proxy_name: str = DEFAULT_PROXY_NAME,
) -> str:
    """Observe one topology sample and reconnect if the selected backend changed.

    On process startup `previous_backend` is None, so one RECONNECT is issued
    deliberately. This heals a stale PgBouncer pool even if the guard itself
    was restarted after a switchover.
    """
    payload = haproxy_stats(haproxy_socket)
    current = parse_primary_backend(payload, proxy_name=proxy_name)
    if current == previous_backend:
        return current

    LOG.warning(
        "HAProxy writable backend changed: %s -> %s; reconnecting PgBouncer database %s",
        previous_backend or "<startup>",
        current,
        database,
    )
    reconnect_pgbouncer(
        database=database,
        socket_dir=pgbouncer_socket_dir,
        port=pgbouncer_port,
        timeout=command_timeout,
        psql=psql,
    )
    LOG.info("PgBouncer RECONNECT/WAIT_CLOSE completed for backend %s", current)
    return current


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--haproxy-socket",
        default=os.environ.get("PGB_PRIMARY_GUARD_HAPROXY_SOCKET", DEFAULT_HAPROXY_SOCKET),
    )
    parser.add_argument(
        "--pgbouncer-socket-dir",
        default=os.environ.get(
            "PGB_PRIMARY_GUARD_PGBOUNCER_SOCKET_DIR", DEFAULT_PGBOUNCER_SOCKET_DIR
        ),
    )
    parser.add_argument(
        "--pgbouncer-port",
        type=int,
        default=int(os.environ.get("PGB_PRIMARY_GUARD_PGBOUNCER_PORT", DEFAULT_PGBOUNCER_PORT)),
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("PGB_PRIMARY_GUARD_DATABASE", DEFAULT_DATABASE),
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.environ.get("PGB_PRIMARY_GUARD_POLL_SECONDS", DEFAULT_POLL_SECONDS)),
    )
    parser.add_argument(
        "--command-timeout",
        type=float,
        default=float(
            os.environ.get("PGB_PRIMARY_GUARD_COMMAND_TIMEOUT", DEFAULT_COMMAND_TIMEOUT)
        ),
    )
    parser.add_argument(
        "--proxy-name",
        default=os.environ.get("PGB_PRIMARY_GUARD_PROXY_NAME", DEFAULT_PROXY_NAME),
        help="HAProxy section serving writes; must match haproxy.cfg",
    )
    parser.add_argument("--psql", default=os.environ.get("PGB_PRIMARY_GUARD_PSQL", "psql"))
    parser.add_argument(
        "--once",
        action="store_true",
        help="observe one sample, reconnect once if needed, then exit",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds must be > 0")
    if args.command_timeout <= 0:
        raise SystemExit("--command-timeout must be > 0")
    if not args.database.replace("_", "").isalnum():
        raise SystemExit("--database contains unsupported characters")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    previous = None
    while True:
        try:
            previous = observe_once(
                previous_backend=previous,
                haproxy_socket=args.haproxy_socket,
                database=args.database,
                pgbouncer_socket_dir=args.pgbouncer_socket_dir,
                pgbouncer_port=args.pgbouncer_port,
                command_timeout=args.command_timeout,
                psql=args.psql,
                proxy_name=args.proxy_name,
            )
        except TopologyAmbiguous as exc:
            # Zero UP can be the normal convergence interval of a switchover;
            # two or more UP is a split-brain/topology incident. In both cases
            # fail closed: never advance state, never RECONNECT to an ambiguous
            # route, and retry forever on the next poll. PatroniPrimaryCountInvalid
            # provides the independent Prometheus/Alertmanager operator signal.
            LOG.warning("Topology not ready; state retained and will be retried: %s", exc)
        except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
            # Crucially, previous is not changed on failure. Once HAProxy and
            # PgBouncer become reachable the same topology transition is retried.
            LOG.error("Primary guard iteration failed; state retained for retry: %s", exc)

        if args.once:
            return 0 if previous is not None else 2
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
