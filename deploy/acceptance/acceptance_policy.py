#!/usr/bin/env python3
"""Validate Stage 4 acceptance criteria against the repository baseline.

The baseline is version-controlled in acceptance-policy.json. Operators may run
stricter criteria without approval. A weaker criterion is fail-closed unless a
complete waiver reference is provided; such a run is explicitly classified as
PASS_WITH_WAIVER rather than PASS.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Mapping

POLICY_PATH = pathlib.Path(__file__).with_name("acceptance-policy.json")


def load_baseline(path: pathlib.Path = POLICY_PATH) -> dict[str, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "search_reindex_documents_min",
        "search_reindex_seconds_max",
        "minio_version_sample_min",
    }
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"acceptance baseline missing keys: {', '.join(sorted(missing))}")
    result = {key: int(payload[key]) for key in required}
    if any(value <= 0 for value in result.values()):
        raise ValueError("acceptance baseline values must be positive integers")
    return result


def evaluate(
    *,
    baseline: Mapping[str, int],
    search_documents: int,
    search_seconds_max: int,
    minio_sample: int,
    waiver_id: str,
    waiver_approver: str,
    waiver_reason: str,
) -> dict:
    values = {
        "search_reindex_documents": int(search_documents),
        "search_reindex_seconds_max": int(search_seconds_max),
        "minio_version_sample": int(minio_sample),
    }
    if any(value <= 0 for value in values.values()):
        return {
            "status": "FAIL",
            "baseline": dict(baseline),
            "effective": values,
            "weakened_criteria": ["invalid_non_positive_value"],
            "waiver_id": waiver_id.strip(),
            "waiver_approver": waiver_approver.strip(),
            "waiver_reason": waiver_reason.strip(),
        }

    weakened: list[str] = []
    if values["search_reindex_documents"] < int(baseline["search_reindex_documents_min"]):
        weakened.append("search_reindex_documents_min")
    if values["search_reindex_seconds_max"] > int(baseline["search_reindex_seconds_max"]):
        weakened.append("search_reindex_seconds_max")
    if values["minio_version_sample"] < int(baseline["minio_version_sample_min"]):
        weakened.append("minio_version_sample_min")

    wid = waiver_id.strip()
    approver = waiver_approver.strip()
    reason = waiver_reason.strip()
    if not weakened:
        status = "PASS"
    elif wid and approver and reason:
        status = "PASS_WITH_WAIVER"
    else:
        status = "FAIL"

    return {
        "status": status,
        "baseline": dict(baseline),
        "effective": values,
        "weakened_criteria": weakened,
        "waiver_id": wid,
        "waiver_approver": approver,
        "waiver_reason": reason,
    }


def _env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", default=str(POLICY_PATH))
    parser.add_argument("--search-documents", type=int)
    parser.add_argument("--search-seconds-max", type=int)
    parser.add_argument("--minio-sample", type=int)
    parser.add_argument("--evidence")
    args = parser.parse_args(argv)

    try:
        baseline = load_baseline(pathlib.Path(args.policy))
        search_documents = (
            args.search_documents
            if args.search_documents is not None
            else _env_positive_int(
                "SEARCH_REINDEX_DOCUMENTS", baseline["search_reindex_documents_min"]
            )
        )
        search_seconds_max = (
            args.search_seconds_max
            if args.search_seconds_max is not None
            else _env_positive_int(
                "SEARCH_REINDEX_MAX_SECONDS", baseline["search_reindex_seconds_max"]
            )
        )
        minio_sample = (
            args.minio_sample
            if args.minio_sample is not None
            else _env_positive_int(
                "MINIO_HASH_SAMPLE_SIZE", baseline["minio_version_sample_min"]
            )
        )
        result = evaluate(
            baseline=baseline,
            search_documents=search_documents,
            search_seconds_max=search_seconds_max,
            minio_sample=minio_sample,
            waiver_id=os.environ.get("ACCEPTANCE_WAIVER_ID", ""),
            waiver_approver=os.environ.get("ACCEPTANCE_WAIVER_APPROVER", ""),
            waiver_reason=os.environ.get("ACCEPTANCE_WAIVER_REASON", ""),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {
            "status": "FAIL",
            "error": str(exc),
            "weakened_criteria": ["policy_evaluation_error"],
        }

    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    if args.evidence:
        pathlib.Path(args.evidence).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result.get("status") in {"PASS", "PASS_WITH_WAIVER"} else 1


if __name__ == "__main__":
    sys.exit(main())
