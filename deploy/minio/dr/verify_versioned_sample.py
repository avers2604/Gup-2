#!/usr/bin/env python3
"""Verify a deterministic random sample of replicated immutable object versions."""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


@dataclass(frozen=True)
class ObjectVersion:
    key: str
    version_id: str


def _client(*, endpoint: str, access_key: str, secret_key: str, verify):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        verify=verify,
        config=Config(
            connect_timeout=10,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )


def list_object_versions(client, bucket: str) -> list[ObjectVersion]:
    versions: list[ObjectVersion] = []
    paginator = client.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket):
        for row in page.get("Versions", []):
            key = row.get("Key")
            version_id = row.get("VersionId")
            if key and version_id and version_id != "null":
                versions.append(ObjectVersion(str(key), str(version_id)))
    return versions


def _sha256(client, bucket: str, item: ObjectVersion) -> str:
    body = client.get_object(
        Bucket=bucket, Key=item.key, VersionId=item.version_id
    )["Body"]
    digest = hashlib.sha256()
    try:
        while True:
            chunk = body.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        body.close()
    return digest.hexdigest()


def _retention(client, bucket: str, item: ObjectVersion) -> tuple[str, str]:
    try:
        row = client.get_object_retention(
            Bucket=bucket, Key=item.key, VersionId=item.version_id
        ).get("Retention", {})
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"NoSuchKey", "NoSuchVersion", "NoSuchObjectLockConfiguration", "InvalidRequest"}:
            return "", ""
        raise
    mode = str(row.get("Mode") or "")
    until = row.get("RetainUntilDate")
    if isinstance(until, datetime):
        until = until.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return mode, str(until or "")


def _legal_hold(client, bucket: str, item: ObjectVersion) -> str:
    try:
        row = client.get_object_legal_hold(
            Bucket=bucket, Key=item.key, VersionId=item.version_id
        ).get("LegalHold", {})
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"NoSuchKey", "NoSuchVersion", "NoSuchObjectLockConfiguration", "InvalidRequest"}:
            return ""
        raise
    return str(row.get("Status") or "")


def verify_one(source, target, bucket: str, item: ObjectVersion) -> dict[str, str]:
    row = {
        "key": item.key,
        "source_version_id": item.version_id,
        "target_version_id": item.version_id,
        "source_sha256": "",
        "target_sha256": "",
        "source_retention_mode": "",
        "target_retention_mode": "",
        "source_retain_until": "",
        "target_retain_until": "",
        "source_legal_hold": "",
        "target_legal_hold": "",
        "reason": "",
        "result": "FAIL",
    }
    try:
        # Bucket replication must preserve the source version ID. Reading target
        # by the same ID proves both version existence and ID equality.
        row["source_sha256"] = _sha256(source, bucket, item)
        row["target_sha256"] = _sha256(target, bucket, item)
        source_mode, source_until = _retention(source, bucket, item)
        target_mode, target_until = _retention(target, bucket, item)
        row["source_retention_mode"] = source_mode
        row["target_retention_mode"] = target_mode
        row["source_retain_until"] = source_until
        row["target_retain_until"] = target_until
        row["source_legal_hold"] = _legal_hold(source, bucket, item)
        row["target_legal_hold"] = _legal_hold(target, bucket, item)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", "ClientError"))
        row["reason"] = f"target/source version lookup failed: {code}"
        return row

    mismatches = []
    if row["source_sha256"] != row["target_sha256"]:
        mismatches.append("sha256")
    if row["source_retention_mode"] != row["target_retention_mode"]:
        mismatches.append("retention_mode")
    if row["source_retain_until"] != row["target_retain_until"]:
        mismatches.append("retain_until")
    if row["source_legal_hold"] != row["target_legal_hold"]:
        mismatches.append("legal_hold")

    # originals is WORM: every sampled source version must have either dated
    # retention or an active legal hold. Equality of two unprotected copies is
    # not sufficient acceptance evidence.
    protected = bool(
        row["source_retention_mode"] and row["source_retain_until"]
    ) or row["source_legal_hold"] == "ON"
    if not protected:
        mismatches.append("source_not_object_locked")

    if mismatches:
        row["reason"] = ";".join(mismatches)
        return row
    row["result"] = "PASS"
    return row


def run(source, target, *, bucket: str, sample_size: int, seed: str, evidence: str) -> tuple[int, int]:
    versions = list_object_versions(source, bucket)
    if len(versions) < sample_size:
        raise RuntimeError(
            f"need at least {sample_size} non-null object versions for acceptance sample; found {len(versions)}"
        )
    sample = random.Random(seed).sample(versions, sample_size)
    rows = [verify_one(source, target, bucket, item) for item in sample]
    fieldnames = [
        "key", "source_version_id", "target_version_id",
        "source_sha256", "target_sha256",
        "source_retention_mode", "target_retention_mode",
        "source_retain_until", "target_retain_until",
        "source_legal_hold", "target_legal_hold", "reason", "result",
    ]
    with open(evidence, "w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    failures = sum(row["result"] != "PASS" for row in rows)
    return len(rows), failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-size", type=int, default=int(os.environ.get("MINIO_HASH_SAMPLE_SIZE", "500")))
    parser.add_argument("--seed", default=os.environ.get("MINIO_HASH_SAMPLE_SEED", "stage4"))
    parser.add_argument("--evidence", default=os.environ.get("MINIO_HASH_EVIDENCE_FILE", "minio-hash-sample.csv"))
    args = parser.parse_args()
    if args.sample_size <= 0:
        parser.error("--sample-size must be > 0")

    required = [
        "MINIO_SOURCE_URL", "MINIO_TARGET_URL",
        "MINIO_SOURCE_ACCESS_KEY", "MINIO_SOURCE_SECRET_KEY",
        "MINIO_TARGET_ACCESS_KEY", "MINIO_TARGET_SECRET_KEY",
        "MINIO_BUCKET_ORIGINALS",
    ]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit("missing environment: " + ", ".join(missing))
    verify = os.environ.get("MINIO_CA_FILE") or True
    source = _client(
        endpoint=os.environ["MINIO_SOURCE_URL"],
        access_key=os.environ["MINIO_SOURCE_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SOURCE_SECRET_KEY"],
        verify=verify,
    )
    target = _client(
        endpoint=os.environ["MINIO_TARGET_URL"],
        access_key=os.environ["MINIO_TARGET_ACCESS_KEY"],
        secret_key=os.environ["MINIO_TARGET_SECRET_KEY"],
        verify=verify,
    )
    try:
        checked, failures = run(
            source,
            target,
            bucket=os.environ["MINIO_BUCKET_ORIGINALS"],
            sample_size=args.sample_size,
            seed=args.seed,
            evidence=args.evidence,
        )
    except (ClientError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if failures:
        print(
            f"ERROR: MinIO versioned sample failed: checked={checked} failures={failures} evidence={args.evidence}",
            file=sys.stderr,
        )
        return 1
    print(
        f"MinIO versioned sample PASS: checked={checked} failures=0 bucket={os.environ['MINIO_BUCKET_ORIGINALS']} evidence={args.evidence}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
