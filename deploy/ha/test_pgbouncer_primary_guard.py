#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import unittest
from unittest.mock import patch

MODULE_PATH = pathlib.Path(__file__).with_name("pgbouncer_primary_guard.py")
spec = importlib.util.spec_from_file_location("pgbouncer_primary_guard", MODULE_PATH)
guard = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(guard)


def stat(*rows):
    header = "# pxname,svname,status,type"
    return "\n".join([header, *rows]) + "\n"


class PrimaryGuardTests(unittest.TestCase):
    def test_single_up_backend_is_selected(self):
        payload = stat(
            "postgres_primary,FRONTEND,OPEN,0",
            "postgres_primary,db1,DOWN,2",
            "postgres_primary,db2,UP,2",
            "postgres_primary,db3,DOWN,2",
            "postgres_primary,BACKEND,UP,1",
        )
        self.assertEqual(guard.parse_primary_backend(payload), "db2")

    def test_zero_or_multiple_up_backends_fail_closed(self):
        with self.assertRaises(guard.TopologyAmbiguous):
            guard.parse_primary_backend(stat("postgres_primary,db1,DOWN,2"))
        with self.assertRaises(guard.TopologyAmbiguous):
            guard.parse_primary_backend(
                stat(
                    "postgres_primary,db1,UP,2",
                    "postgres_primary,db2,UP,2",
                )
            )

    @patch.object(guard, "reconnect_pgbouncer")
    @patch.object(guard, "haproxy_stats")
    def test_startup_reconnects_once_and_steady_state_does_not(self, stats, reconnect):
        stats.return_value = stat("postgres_primary,db1,UP,2")
        current = guard.observe_once(
            previous_backend=None,
            haproxy_socket="/tmp/haproxy.sock",
            database="bz_get",
            pgbouncer_socket_dir="/tmp",
            pgbouncer_port=6432,
            command_timeout=60,
            psql="psql",
        )
        self.assertEqual(current, "db1")
        reconnect.assert_called_once()

        reconnect.reset_mock()
        current = guard.observe_once(
            previous_backend=current,
            haproxy_socket="/tmp/haproxy.sock",
            database="bz_get",
            pgbouncer_socket_dir="/tmp",
            pgbouncer_port=6432,
            command_timeout=60,
            psql="psql",
        )
        self.assertEqual(current, "db1")
        reconnect.assert_not_called()

    @patch.object(guard, "reconnect_pgbouncer")
    @patch.object(guard, "haproxy_stats")
    def test_backend_change_reconnects_before_state_advances(self, stats, reconnect):
        stats.return_value = stat("postgres_primary,db2,UP,2")
        current = guard.observe_once(
            previous_backend="db1",
            haproxy_socket="/tmp/haproxy.sock",
            database="bz_get",
            pgbouncer_socket_dir="/tmp",
            pgbouncer_port=6432,
            command_timeout=60,
            psql="psql",
        )
        self.assertEqual(current, "db2")
        reconnect.assert_called_once_with(
            database="bz_get",
            socket_dir="/tmp",
            port=6432,
            timeout=60,
            psql="psql",
        )

    @patch.object(guard, "haproxy_stats")
    def test_reconnect_failure_is_not_mistaken_for_success(self, stats):
        stats.return_value = stat("postgres_primary,db2,UP,2")
        with patch.object(
            guard,
            "reconnect_pgbouncer",
            side_effect=subprocess.CalledProcessError(1, ["psql"]),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                guard.observe_once(
                    previous_backend="db1",
                    haproxy_socket="/tmp/haproxy.sock",
                    database="bz_get",
                    pgbouncer_socket_dir="/tmp",
                    pgbouncer_port=6432,
                    command_timeout=60,
                    psql="psql",
                )

    @patch.object(guard.subprocess, "run")
    def test_reconnect_uses_reconnect_then_wait_close(self, run):
        guard.reconnect_pgbouncer(
            database="bz_get",
            socket_dir="/var/run/postgresql",
            port=6432,
            timeout=60,
            psql="/usr/bin/psql",
        )
        args = run.call_args.args[0]
        self.assertIn("RECONNECT bz_get;", args)
        self.assertIn("WAIT_CLOSE bz_get;", args)
        self.assertIn("-U", args)
        self.assertEqual(args[args.index("-U") + 1], "pgbouncer")
        self.assertTrue(run.call_args.kwargs["check"])


if __name__ == "__main__":
    unittest.main()
