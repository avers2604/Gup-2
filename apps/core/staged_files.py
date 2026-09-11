"""Transactional staging for immutable files.

Django FileField writes object storage before the surrounding PostgreSQL
transaction commits. That ordering is unacceptable for an Object-Locked bucket:
a later database rollback would leave an immutable orphan that cannot be
compensated with DELETE.

Uploads that ultimately belong in ``originals`` therefore follow this path:

    upload -> mutable ``working/staging/worm`` -> DB commit -> durable task
           -> server-side copy to ``originals`` with Object Lock -> staging delete

The database stores the final destination key from the start, but the durable
``StagedFilePromotion`` row is committed in the same transaction. The promotion
worker is idempotent and verifies both the copied bytes and the requested Object
Lock state before it marks the promotion complete.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timezone as dt_timezone
from pathlib import PurePosixPath

from botocore.exceptions import ClientError
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import StagedFilePromotion
from .outbox import enqueue
from .storage import originals_storage, working_storage

logger = logging.getLogger(__name__)
_PENDING_ATTR = "_bz_get_staged_file_promotions"
_STAGING_PREFIX = "staging/worm"


@dataclass(frozen=True)
class PendingPromotion:
    field_name: str
    staging_name: str
    destination_name: str


def _unique_destination_name(generated_name: str) -> str:
    path = PurePosixPath(generated_name)
    token = uuid.uuid4().hex
    suffix = path.suffix.lower()
    stem = path.name[: -len(path.suffix)] if path.suffix else path.name
    return str(path.with_name(f"{stem}-{token}{suffix}"))


def stage_uploaded_field(instance, field_name: str, *, update_fields=None) -> PendingPromotion | None:
    """Move one uncommitted FieldFile into mutable staging before DB write.

    ``FileField.pre_save`` normally writes an uncommitted upload to its own
    storage. We stage it earlier from the model ``pre_save`` signal, replace the
    in-memory name with a unique final key, and mark the FieldFile committed so
    Django stores only that final key in PostgreSQL without touching WORM yet.
    """
    if update_fields is not None and field_name not in set(update_fields):
        return None

    field_file = getattr(instance, field_name, None)
    if not field_file or getattr(field_file, "_committed", True):
        return None

    # Do not allow a second immutable replacement to overtake one that is
    # already durably committed but has not reached WORM yet. Without this
    # guard two final keys could be promoted even though only the newer one is
    # referenced by the model. The window is normally seconds and is surfaced
    # by the pending-promotion metrics.
    if instance.pk and StagedFilePromotion.objects.filter(
        model_label=instance._meta.label_lower,
        object_id=str(instance.pk),
        field_name=field_name,
        completed_at__isnull=True,
    ).exists():
        raise ValidationError(
            {field_name: "Предыдущая загрузка ещё переносится в WORM-хранилище. Повторите после завершения."}
        )

    # A second save of the same Python object inside one still-open transaction
    # must not create another staging object for this field. post_save keeps
    # the pending intent on the instance until the outermost transaction
    # commits specifically so rollback + retry remains safe.
    current = list(getattr(instance, _PENDING_ATTR, ()))
    existing = next((item for item in current if item.field_name == field_name), None)
    if existing is not None:
        field_file.name = existing.destination_name
        field_file._committed = True
        return existing

    model_field = instance._meta.get_field(field_name)
    generated = model_field.generate_filename(instance, field_file.name)
    destination_name = _unique_destination_name(generated)
    staging_name = (
        f"{_STAGING_PREFIX}/{instance._meta.app_label}/{instance._meta.model_name}/"
        f"{uuid.uuid4().hex}/{PurePosixPath(destination_name).name}"
    )

    content = field_file.file
    if hasattr(content, "seek"):
        content.seek(0)
    saved_staging_name = working_storage().save(staging_name, content)
    if hasattr(content, "seek"):
        content.seek(0)

    # Prevent FileField.pre_save() from writing directly to originals.
    field_file.name = destination_name
    field_file._committed = True

    pending = PendingPromotion(
        field_name=field_name,
        staging_name=saved_staging_name,
        destination_name=destination_name,
    )
    current.append(pending)
    setattr(instance, _PENDING_ATTR, current)
    return pending


def _lock_values(instance) -> tuple[str, datetime | None, bool]:
    """Snapshot legal retention into values that can be persisted with the intent."""
    from apps.documents.retention import (
        RETENTION_MATRIX,
        RetentionCategory,
        object_lock_params_for,
    )

    if instance._meta.label_lower == "documents.normativedocument":
        policy = RETENTION_MATRIX[instance.retention_category]
        retention_until = instance.retention_until
    elif instance._meta.label_lower == "templates_bank.template":
        policy = RETENTION_MATRIX[RetentionCategory.TEMPLATES_APPROVED]
        retention_until = None
    else:  # Defensive: only explicit WORM model signals may call this function.
        raise ValueError(f"unsupported WORM model: {instance._meta.label_lower}")

    params = object_lock_params_for(policy, retention_until)
    lock_mode = params.get("ObjectLockMode") or ""
    legal_hold = params.get("ObjectLockLegalHoldStatus") == "ON"
    raw_until = params.get("ObjectLockRetainUntilDate")
    retain_until = None

    if raw_until:
        value = date.fromisoformat(raw_until) if isinstance(raw_until, str) else raw_until
        # A document imported after its nominal retention date still requires
        # an explicit curator/controller decision (retention.py). S3 must not
        # receive an already-expired retain-until timestamp because that would
        # make the new object immediately removable. Fail safe with legal hold.
        if value < timezone.localdate():
            lock_mode = ""
            legal_hold = True
        else:
            retain_until = datetime.combine(value, time.max, tzinfo=dt_timezone.utc)

    return lock_mode, retain_until, legal_hold


def _clear_pending_after_commit(instance) -> None:
    setattr(instance, _PENDING_ATTR, [])


def _queue_pending_promotions(instance) -> list[StagedFilePromotion]:
    pending = list(getattr(instance, _PENDING_ATTR, ()))
    if not pending:
        return []

    lock_mode, retain_until, legal_hold = _lock_values(instance)
    rows = []
    for item in pending:
        promotion, created = StagedFilePromotion.objects.get_or_create(
            model_label=instance._meta.label_lower,
            object_id=str(instance.pk),
            field_name=item.field_name,
            destination_name=item.destination_name,
            defaults={
                "staging_name": item.staging_name,
                "lock_mode": lock_mode,
                "retain_until": retain_until,
                "legal_hold": legal_hold,
            },
        )
        if promotion.staging_name != item.staging_name:
            raise RuntimeError(
                f"promotion identity collision for {instance._meta.label_lower}:{instance.pk}:{item.field_name}"
            )
        if created:
            enqueue("apps.core.tasks.promote_staged_file", [str(promotion.pk)])
        rows.append(promotion)

    # Do not clear immediately: PostgreSQL can still roll back after post_save,
    # while the in-memory model object and its FieldFile do not. Keeping the
    # intent until outer commit makes a retry on the same object recreate the
    # durable row instead of persisting a final key with no promotion record.
    transaction.on_commit(lambda: _clear_pending_after_commit(instance))
    return rows


def discard_staged_uploads(instance) -> None:
    """Best-effort cleanup for a caller that knows its DB transaction failed."""
    pending = list(getattr(instance, _PENDING_ATTR, ()))
    for item in pending:
        _delete_staging_object(item.staging_name)
    setattr(instance, _PENDING_ATTR, [])


@receiver(pre_save, sender="documents.NormativeDocument", dispatch_uid="stage_nrd_original")
def _stage_document_original(sender, instance, raw=False, update_fields=None, **kwargs):
    if not raw:
        stage_uploaded_field(instance, "files_original", update_fields=update_fields)


@receiver(pre_save, sender="templates_bank.Template", dispatch_uid="stage_template_worm_files")
def _stage_template_files(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw:
        return
    stage_uploaded_field(instance, "file_editable", update_fields=update_fields)
    stage_uploaded_field(instance, "file_sample", update_fields=update_fields)


@receiver(post_save, sender="documents.NormativeDocument", dispatch_uid="queue_nrd_original_promotion")
@receiver(post_save, sender="templates_bank.Template", dispatch_uid="queue_template_worm_promotions")
def _queue_worm_promotions(sender, instance, raw=False, **kwargs):
    if not raw:
        _queue_pending_promotions(instance)


def _hash_object(storage, name: str) -> str:
    digest = hashlib.sha256()
    with storage.open(name, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _is_s3_storage(storage) -> bool:
    return bool(getattr(storage, "bucket_name", None) and getattr(storage, "connection", None))


def _not_found(exc: ClientError) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str(response.get("Error", {}).get("Code", ""))
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status == 404 or code in {"404", "NoSuchKey", "NotFound"}


def _verify_object_lock(client, bucket: str, key: str, promotion: StagedFilePromotion, head: dict) -> None:
    if promotion.lock_mode:
        actual_mode = head.get("ObjectLockMode")
        if actual_mode != promotion.lock_mode:
            retention = client.get_object_retention(Bucket=bucket, Key=key).get("Retention", {})
            actual_mode = retention.get("Mode")
            actual_until = retention.get("RetainUntilDate")
        else:
            actual_until = head.get("ObjectLockRetainUntilDate")
        if actual_mode != promotion.lock_mode:
            raise RuntimeError(
                f"Object Lock mode mismatch for {key}: {actual_mode!r} != {promotion.lock_mode!r}"
            )
        if promotion.retain_until:
            if actual_until is None:
                retention = client.get_object_retention(Bucket=bucket, Key=key).get("Retention", {})
                actual_until = retention.get("RetainUntilDate")
            if actual_until is None or actual_until < promotion.retain_until:
                raise RuntimeError(f"Object Lock retain-until is weaker than requested for {key}")

    if promotion.legal_hold:
        hold = head.get("ObjectLockLegalHoldStatus")
        if hold != "ON":
            hold = client.get_object_legal_hold(Bucket=bucket, Key=key).get("LegalHold", {}).get("Status")
        if hold != "ON":
            raise RuntimeError(f"Object legal hold is not ON for {key}")


def _copy_s3(promotion: StagedFilePromotion, source, destination) -> str:
    client = destination.connection.meta.client
    bucket = destination.bucket_name
    source_bucket = source.bucket_name

    try:
        existing = client.head_object(Bucket=bucket, Key=promotion.destination_name)
    except ClientError as exc:
        if not _not_found(exc):
            raise
        existing = None

    # A worker can die after the immutable copy but before the DB transaction
    # records completion. The metadata marker makes retry idempotent and avoids
    # producing another WORM version for the same promotion.
    if existing is not None:
        marker = (existing.get("Metadata") or {}).get("bz-promotion-id")
        if marker != str(promotion.pk):
            raise RuntimeError(
                f"destination collision for {promotion.destination_name}: promotion marker={marker!r}"
            )
        _verify_object_lock(client, bucket, promotion.destination_name, promotion, existing)
        return _hash_object(destination, promotion.destination_name)

    lock_config = client.get_object_lock_configuration(Bucket=bucket)
    if lock_config.get("ObjectLockConfiguration", {}).get("ObjectLockEnabled") != "Enabled":
        raise RuntimeError(f"destination bucket {bucket!r} has Object Lock disabled")

    source_hash = _hash_object(source, promotion.staging_name)
    source_head = client.head_object(Bucket=source_bucket, Key=promotion.staging_name)
    metadata = dict(source_head.get("Metadata") or {})
    metadata.update({
        "bz-promotion-id": str(promotion.pk),
        "bz-sha256": source_hash,
    })

    copy_args = {
        "Bucket": bucket,
        "Key": promotion.destination_name,
        "CopySource": {"Bucket": source_bucket, "Key": promotion.staging_name},
        "MetadataDirective": "REPLACE",
        "Metadata": metadata,
        "ContentType": source_head.get("ContentType") or "application/octet-stream",
    }
    if promotion.lock_mode:
        copy_args["ObjectLockMode"] = promotion.lock_mode
    if promotion.retain_until:
        copy_args["ObjectLockRetainUntilDate"] = promotion.retain_until
    if promotion.legal_hold:
        copy_args["ObjectLockLegalHoldStatus"] = "ON"

    client.copy_object(**copy_args)
    head = client.head_object(Bucket=bucket, Key=promotion.destination_name)
    if (head.get("Metadata") or {}).get("bz-promotion-id") != str(promotion.pk):
        raise RuntimeError(f"promotion metadata verification failed for {promotion.destination_name}")
    _verify_object_lock(client, bucket, promotion.destination_name, promotion, head)

    destination_hash = _hash_object(destination, promotion.destination_name)
    if destination_hash != source_hash:
        raise RuntimeError(
            f"SHA-256 mismatch after WORM promotion: {source_hash} != {destination_hash}"
        )
    return destination_hash


def _copy_filesystem(promotion: StagedFilePromotion, source, destination) -> str:
    """Development/test fallback; production uses S3 and verifies Object Lock."""
    source_hash = _hash_object(source, promotion.staging_name)
    if destination.exists(promotion.destination_name):
        destination_hash = _hash_object(destination, promotion.destination_name)
        if destination_hash != source_hash:
            raise RuntimeError(f"destination collision for {promotion.destination_name}")
        return destination_hash

    with source.open(promotion.staging_name, "rb") as stream:
        saved_name = destination.save(promotion.destination_name, stream)
    if saved_name != promotion.destination_name:
        raise RuntimeError(
            f"destination storage renamed immutable object: {saved_name!r} != {promotion.destination_name!r}"
        )
    destination_hash = _hash_object(destination, promotion.destination_name)
    if destination_hash != source_hash:
        raise RuntimeError("SHA-256 mismatch after development promotion")
    return destination_hash


def _delete_staging_object(name: str) -> None:
    try:
        working_storage().delete(name)
    except Exception:
        # The immutable destination is already committed and verified. A stale
        # staging copy is safe and is covered by the staging-prefix lifecycle;
        # do not turn cleanup failure into a false promotion failure.
        logger.exception("Failed to delete staging object after WORM promotion: %s", name)


def promote_staged_file_once(promotion_id) -> str:
    """Copy one staged object to WORM storage exactly once from DB perspective."""
    source = working_storage()
    destination = originals_storage()

    with transaction.atomic():
        promotion = StagedFilePromotion.objects.select_for_update().get(pk=promotion_id)
        if promotion.completed_at is not None:
            return promotion.sha256

        promotion.attempts += 1
        promotion.last_error = ""
        promotion.save(update_fields=["attempts", "last_error"])

        if _is_s3_storage(source) and _is_s3_storage(destination):
            digest = _copy_s3(promotion, source, destination)
        else:
            digest = _copy_filesystem(promotion, source, destination)

        # Update the document checksum only if this promotion still represents
        # the current original. A later replacement must not receive the old
        # object's digest when an earlier delayed promotion finally completes.
        if (
            promotion.model_label == "documents.normativedocument"
            and promotion.field_name == "files_original"
        ):
            model = apps.get_model("documents", "NormativeDocument")
            model.objects.filter(
                pk=promotion.object_id,
                files_original=promotion.destination_name,
            ).update(files_original_sha256=digest)

        promotion.sha256 = digest
        promotion.completed_at = timezone.now()
        promotion.save(update_fields=["sha256", "completed_at", "last_error"])
        staging_name = promotion.staging_name
        transaction.on_commit(lambda: _delete_staging_object(staging_name))

    return digest


def record_promotion_error(promotion_id, exc: Exception) -> None:
    StagedFilePromotion.objects.filter(pk=promotion_id, completed_at__isnull=True).update(
        attempts=F("attempts") + 1,
        last_error=f"{type(exc).__name__}: {exc}"[:500],
    )


def promotion_pending_for(model_label: str, object_id: str, field_name: str, destination_name: str) -> bool:
    return StagedFilePromotion.objects.filter(
        model_label=model_label,
        object_id=str(object_id),
        field_name=field_name,
        destination_name=destination_name,
        completed_at__isnull=True,
    ).exists()
