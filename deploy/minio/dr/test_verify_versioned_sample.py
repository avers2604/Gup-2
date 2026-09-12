#!/usr/bin/env python3
from __future__ import annotations

import csv
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
    def __init__(
        self,
        *,
        payload=b"payload",
        protected=True,
        legal_hold="OFF",
        versions=None,
        missing=False,
        bucket_versioning=True,
        object_lock=True,
    ):
        self.payload = payload
        self.protected = protected
        self.legal_hold = legal_hold
        self.versions = versions or [{"Key": "a.pdf", "VersionId": "v1"}]
        self.missing = missing
        self.bucket_versioning = bucket_versioning
        self.object_lock = object_lock

    def get_paginator(self, name):
        assert name == "list_object_versions"
        return FakePaginator(self.versions)

    def get_bucket_versioning(self, **kwargs):
        return {"Status": "Enabled" if self.bucket_versioning else "Suspended"}

    def get_object_lock_configuration(self, **kwargs):
        if not self.object_lock:
            raise ClientError(
                {"Error": {"Code": "ObjectLockConfigurationNotFoundError", "Message": "none"}},
                "GetObjectLockConfiguration",
            )
        return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}

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
        return {"LegalHold": {"Status": self.legal_hold}}


class VersionedSampleTests(unittest.TestCase):
    def test_same_version_bytes_and_object_lock_pass(self):
        item = verifier.ObjectVersion("a.pdf", "v1")
        row = verifier.verify_one(FakeS3(), FakeS3(), "originals", item)
        self.assertEqual(row["result"], "PASS")
        self.assertEqual(row["source_version_id"], "v1")
        self.assertEqual(row["target_version_id"], "v1")
        self.assertEqual(row["source_retention_mode"], "COMPLIANCE")

    def test_legal_hold_without_retain_until_is_valid_permanent_protection(self):
        item = verifier.ObjectVersion("permanent.pdf", "v7")
        source = FakeS3(protected=False, legal_hold="ON")
        target = FakeS3(protected=False, legal_hold="ON")
        row = verifier.verify_one(source, target, "originals", item)
        self.assertEqual(row["source_retain_until"], "")
        self.assertEqual(row["source_legal_hold"], "ON")
        self.assertEqual(row["result"], "PASS")

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
        self.assertEqual(row["target_version_id"], "")

    def test_acceptance_bucket_must_have_versioning_and_object_lock(self):
        with self.assertRaisesRegex(RuntimeError, "Versioning"):
            verifier.validate_acceptance_bucket(FakeS3(bucket_versioning=False), "originals")
        with self.assertRaisesRegex(RuntimeError, "Object Lock"):
            verifier.validate_acceptance_bucket(FakeS3(object_lock=False), "originals")
        verifier.validate_acceptance_bucket(FakeS3(), "originals")

    def test_same_seed_is_reproducible_even_if_list_order_changes(self):
        versions = [
            {"Key": f"obj-{index}.bin", "VersionId": f"v{index}"}
            for index in range(12)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            first = pathlib.Path(tmp) / "first.csv"
            second = pathlib.Path(tmp) / "second.csv"
            verifier.run(
                FakeS3(versions=versions), FakeS3(versions=versions),
                bucket="originals", sample_size=5, seed="RUN-42", evidence=str(first),
            )
            reversed_versions = list(reversed(versions))
            verifier.run(
                FakeS3(versions=reversed_versions), FakeS3(versions=versions),
                bucket="originals", sample_size=5, seed="RUN-42", evidence=str(second),
            )
            with first.open(newline="", encoding="utf-8") as handle:
                first_rows = list(csv.DictReader(handle))
            with second.open(newline="", encoding="utf-8") as handle:
                second_rows = list(csv.DictReader(handle))
        self.assertEqual(
            [(row["key"], row["source_version_id"]) for row in first_rows],
            [(row["key"], row["source_version_id"]) for row in second_rows],
        )
        self.assertTrue(all(row["sample_seed"] == "RUN-42" for row in first_rows))
        self.assertTrue(all(row["sample_algorithm"] == "sorted-random-v1" for row in first_rows))

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
            self.assertIn("sample_seed", header)
            self.assertIn("sample_algorithm", header)


if __name__ == "__main__":
    unittest.main()
