#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone

from botocore.exceptions import ClientError

MODULE_PATH = pathlib.Path(__file__).with_name("verify_versioned_sample.py")
spec = importlib.util.spec_from_file_location("verify_versioned_sample", MODULE_PATH)
verifier = importlib.util.module_from_spec(spec)
assert spec.loader is not None
# dataclasses resolves postponed annotations through sys.modules while the
# module is being executed; register the dynamic module exactly as import does.
sys.modules[spec.name] = verifier
spec.loader.exec_module(verifier)


class Body(io.BytesIO):
    def close(self):
        super().close()


class FakePaginator:
    def __init__(self, rows):
        self.rows = rows

    def paginate(self, **kwargs):
        yield {"Versions": self.rows}


class FakeS3:
    def __init__(self, *, payload=b"payload", protected=True, versions=None, missing=False):
        self.payload = payload
        self.protected = protected
        self.versions = versions or [{"Key": "a.pdf", "VersionId": "v1"}]
        self.missing = missing

    def get_paginator(self, name):
        assert name == "list_object_versions"
        return FakePaginator(self.versions)

    def get_object(self, **kwargs):
        if self.missing:
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchVersion", "Message": "missing"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "GetObject",
            )
        return {"Body": Body(self.payload)}

    def get_object_retention(self, **kwargs):
        if not self.protected:
            return {"Retention": {}}
        return {
            "Retention": {
                "Mode": "COMPLIANCE",
                "RetainUntilDate": datetime(2030, 1, 1, tzinfo=timezone.utc),
            }
        }

    def get_object_legal_hold(self, **kwargs):
        return {"LegalHold": {"Status": "OFF"}}


class VersionedSampleTests(unittest.TestCase):
    def test_same_version_bytes_and_object_lock_pass(self):
        item = verifier.ObjectVersion("a.pdf", "v1")
        row = verifier.verify_one(FakeS3(), FakeS3(), "originals", item)
        self.assertEqual(row["result"], "PASS")
        self.assertEqual(row["source_version_id"], "v1")
        self.assertEqual(row["target_version_id"], "v1")
        self.assertEqual(row["source_retention_mode"], "COMPLIANCE")

    def test_equal_but_unprotected_versions_fail(self):
        item = verifier.ObjectVersion("a.pdf", "v1")
        row = verifier.verify_one(
            FakeS3(protected=False), FakeS3(protected=False), "originals", item
        )
        self.assertEqual(row["result"], "FAIL")
        self.assertIn("source_not_object_locked", row["reason"])

    def test_missing_target_version_fails(self):
        item = verifier.ObjectVersion("a.pdf", "v1")
        row = verifier.verify_one(FakeS3(), FakeS3(missing=True), "originals", item)
        self.assertEqual(row["result"], "FAIL")
        self.assertIn("NoSuchVersion", row["reason"])

    def test_run_writes_one_evidence_row_per_sampled_version(self):
        versions = [
            {"Key": f"obj-{index}.bin", "VersionId": f"v{index}"}
            for index in range(5)
        ]
        source = FakeS3(versions=versions)
        target = FakeS3(versions=versions)
        with tempfile.TemporaryDirectory() as tmp:
            evidence = pathlib.Path(tmp) / "sample.csv"
            checked, failures = verifier.run(
                source,
                target,
                bucket="originals",
                sample_size=3,
                seed="run-42",
                evidence=str(evidence),
            )
            self.assertEqual((checked, failures), (3, 0))
            self.assertEqual(len(evidence.read_text().splitlines()), 4)
            header = evidence.read_text().splitlines()[0]
            self.assertIn("source_version_id", header)
            self.assertIn("source_retention_mode", header)
            self.assertIn("source_legal_hold", header)


if __name__ == "__main__":
    unittest.main()
