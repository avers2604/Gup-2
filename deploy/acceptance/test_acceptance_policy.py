#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).with_name("acceptance_policy.py")
spec = importlib.util.spec_from_file_location("acceptance_policy", MODULE_PATH)
policy = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(policy)

BASELINE = {
    "search_reindex_documents_min": 10000,
    "search_reindex_seconds_max": 3600,
    "minio_version_sample_min": 500,
}


class AcceptancePolicyTests(unittest.TestCase):
    def test_baseline_is_pass(self):
        result = policy.evaluate(
            baseline=BASELINE,
            search_documents=10000,
            search_seconds_max=3600,
            minio_sample=500,
            waiver_id="",
            waiver_approver="",
            waiver_reason="",
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["weakened_criteria"], [])

    def test_stricter_values_do_not_need_waiver(self):
        result = policy.evaluate(
            baseline=BASELINE,
            search_documents=15000,
            search_seconds_max=1800,
            minio_sample=750,
            waiver_id="",
            waiver_approver="",
            waiver_reason="",
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["weakened_criteria"], [])

    def test_weaker_values_fail_without_waiver(self):
        result = policy.evaluate(
            baseline=BASELINE,
            search_documents=5000,
            search_seconds_max=7200,
            minio_sample=120,
            waiver_id="",
            waiver_approver="",
            waiver_reason="",
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(
            set(result["weakened_criteria"]),
            {"search_reindex_documents_min", "search_reindex_seconds_max", "minio_version_sample_min"},
        )

    def test_weaker_values_require_complete_waiver_metadata(self):
        result = policy.evaluate(
            baseline=BASELINE,
            search_documents=5000,
            search_seconds_max=3600,
            minio_sample=120,
            waiver_id="WAIVER-17",
            waiver_approver="Заказчик",
            waiver_reason="На приёмочном стенде доступно 120 версий",
        )
        self.assertEqual(result["status"], "PASS_WITH_WAIVER")
        self.assertEqual(result["waiver_id"], "WAIVER-17")
        self.assertTrue(result["weakened_criteria"])

        incomplete = policy.evaluate(
            baseline=BASELINE,
            search_documents=5000,
            search_seconds_max=3600,
            minio_sample=500,
            waiver_id="WAIVER-17",
            waiver_approver="",
            waiver_reason="",
        )
        self.assertEqual(incomplete["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
