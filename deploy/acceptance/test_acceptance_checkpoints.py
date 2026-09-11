#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest

MODULE_PATH = pathlib.Path(__file__).with_name("acceptance_checkpoints.py")
REPO_ROOT = MODULE_PATH.parents[2]
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

    def test_missing_alertmanager_checkpoint_is_named_in_console_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            path = root / "checkpoints.csv"
            evidence = root / "acceptance-checkpoints.json"
            write_csv(
                path,
                [name for name in REQUIRED if name != "alertmanager-peer-loss-validated"],
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                rc = checkpoints.main([str(path), "--evidence", str(evidence)])

            rendered = stdout.getvalue()
            payload = json.loads(evidence.read_text(encoding="utf-8"))

        self.assertEqual(rc, 1)
        self.assertIn("alertmanager-peer-loss-validated", rendered)
        self.assertEqual(payload["missing"], ["alertmanager-peer-loss-validated"])

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


class DestructiveReindexIsolationTests(unittest.TestCase):
    def test_destructive_opt_in_is_absent_from_production_deployment_surfaces(self):
        marker = "ACCEPTANCE_ALLOW_DESTRUCTIVE_REINDEX"
        candidates: set[pathlib.Path] = set()

        deploy_root = REPO_ROOT / "deploy"
        for path in deploy_root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(deploy_root)
            if relative.parts and relative.parts[0] == "acceptance":
                continue
            candidates.add(path)

        for pattern in (
            ".env*",
            "docker-compose*.yml",
            "docker-compose*.yaml",
            "compose*.yml",
            "compose*.yaml",
        ):
            candidates.update(path for path in REPO_ROOT.glob(pattern) if path.is_file())

        offenders = []
        for path in sorted(candidates):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if marker in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))

        self.assertEqual(
            offenders,
            [],
            "destructive acceptance opt-in leaked into production deployment surfaces: "
            + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
