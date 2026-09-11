#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path


DEFAULT_REQUIRED = [
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


def _valid_utc(value: str) -> bool:
    if not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True


def verify(path: Path, required=DEFAULT_REQUIRED) -> dict:
    errors: list[str] = []
    present: set[str] = set()
    if not path.exists():
        errors.append(f"checkpoint evidence is missing: {path}")
    else:
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames != ["timestamp_utc", "name"]:
                    errors.append("checkpoints.csv must have timestamp_utc,name header")
                else:
                    for row_number, row in enumerate(reader, start=2):
                        timestamp = (row.get("timestamp_utc") or "").strip()
                        name = (row.get("name") or "").strip()
                        if not timestamp or not _valid_utc(timestamp):
                            errors.append(f"row {row_number}: invalid UTC timestamp {timestamp!r}")
                        if not name:
                            errors.append(f"row {row_number}: empty checkpoint name")
                        else:
                            present.add(name)
        except (OSError, csv.Error) as exc:
            errors.append(str(exc))

    required_list = list(required)
    missing = [name for name in required_list if name not in present]
    status = "PASS" if not errors and not missing else "FAIL"
    return {
        "status": status,
        "required": required_list,
        "present": sorted(present),
        "missing": missing,
        "errors": errors,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify required Stage 4 acceptance checkpoints")
    parser.add_argument("checkpoints")
    parser.add_argument("--evidence")
    args = parser.parse_args(argv)

    result = verify(Path(args.checkpoints))
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    if args.evidence:
        Path(args.evidence).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
