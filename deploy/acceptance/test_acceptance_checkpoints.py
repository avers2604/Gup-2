#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest

MODULE_PATH = pathlib.Path(__file__).with_name("acceptance_checkpoints.py")
spec = importlib.util.spec_from_file_location("acceptance_checkpoints", MODULE_PATH)
checkpoints = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(checkpoints)

REQUIRED = [
    "preflight-complete",
    "planned-switchover-complete",
    "unplanned-failover-complete",
    "pitr-validated",
    "replica-rebuild-validated",
    "minio-failover-validated",
    "minio-failback-validated",
    "alertmanager-peer-loss-validated",
    "application-smoke-validated",
    "write-path-switchover-measured",
]


def write_csv(path: pathlib.Path, names):
    rows = ["timestamp_utc,name"]
    for index, name in enumerate(names):
        rows.append(f"2026-09-11T15:{index:02d}:00Z,{name}")
    path.write_text("\n".join(rows) + "\n")


class AcceptanceCheckpointTests(unittest.TestCase):
    def test_complete_required_set_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "checkpoints.csv"
            write_csv(path, REQUIRED)
            result = checkpoints.verify(path, REQUIRED)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["missing"], [])

    def test_missing_real_stand_checkpoint_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "checkpoints.csv"
            write_csv(path, [name for name in REQUIRED if name != "unplanned-failover-complete"])
            result = checkpoints.verify(path, REQUIRED)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["missing"], ["unplanned-failover-complete"])

    def test_extra_checkpoints_do_not_replace_required_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "checkpoints.csv"
            write_csv(path, ["preflight-complete", "some-other-check"])
            result = checkpoints.verify(path, REQUIRED)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("planned-switchover-complete", result["missing"])

    def test_malformed_timestamp_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "checkpoints.csv"
            path.write_text("timestamp_utc,name\nnot-utc,preflight-complete\n")
            result = checkpoints.verify(path, ["preflight-complete"])
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["errors"])


if __name__ == "__main__":
    unittest.main()
