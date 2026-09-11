"""Celery OCR pipeline with late-ack and idempotent final persistence."""
import logging

from celery import shared_task
from django.db import transaction

from apps.audit.models import AuditLog
from apps.core.business_metrics import sync_ocr_review_queue

from .ocr import extract_text_and_confidence
from .ocr_thresholds import get_threshold_for_document

logger = logging.getLogger(__name__)

TASK_TIME_LIMIT = 600
TASK_SOFT_TIME_LIMIT = 540


def _already_finalized(task_id: str) -> bool:
    if not task_id:
        return False
    return AuditLog.objects.filter(
        event_type__in=(
            AuditLog.EventType.DOCUMENT_OCR_COMPLETED,
            AuditLog.EventType.DOCUMENT_OCR_FAILED,
        ),
        details__task_id=task_id,
    ).exists()


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    time_limit=TASK_TIME_LIMIT,
    soft_time_limit=TASK_SOFT_TIME_LIMIT,
    acks_late=True,
    reject_on_worker_lost=True,
    track_started=True,
)
def run_ocr_for_document(self, document_id):
    from .models import NormativeDocument

    task_id = self.request.id or ""
    if _already_finalized(task_id):
        logger.info("run_ocr_for_document: duplicate delivery %s suppressed", task_id)
        return

    try:
        document = NormativeDocument.objects.get(pk=document_id)
    except NormativeDocument.DoesNotExist:
        logger.warning("run_ocr_for_document: документ %s не найден, задача пропущена", document_id)
        return

    source_name = document.files_original.name

    # The model commits the final originals key together with a durable
    # StagedFilePromotion, then the promotion worker copies bytes from mutable
    # staging into Object-Locked storage. The historical OCR outbox entry is
    # still created by NormativeDocument.save(); if workers run concurrently,
    # OCR must wait for that promotion instead of classifying a temporary 404
    # as a permanent OCR failure.
    from apps.core.staged_files import promotion_pending_for

    if promotion_pending_for(
        "documents.normativedocument",
        str(document.pk),
        "files_original",
        source_name,
    ):
        raise self.retry(countdown=5, max_retries=120)

    try:
        with document.files_original.open("rb") as fh:
            from django.conf import settings
            pdf_bytes = fh.read(settings.OCR_MAX_BYTES + 1)
        text, confidence = extract_text_and_confidence(pdf_bytes)
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)

        with transaction.atomic():
            document = NormativeDocument.objects.select_for_update().get(pk=document_id)
            if document.files_original.name != source_name:
                return
            if _already_finalized(task_id):
                return
            document.ocr_status = NormativeDocument.OcrStatus.NEEDS_REVIEW
            document.save(update_fields=["ocr_status"])
            sync_ocr_review_queue(document.pk, needs_review=True)
            AuditLog.objects.create(
                event_type=AuditLog.EventType.DOCUMENT_OCR_FAILED,
                object_type="NormativeDocument",
                object_id=str(document.pk),
                details={
                    "error": str(exc),
                    "retries": self.request.retries,
                    "task_id": task_id,
                },
            )
        return

    threshold = get_threshold_for_document(document)
    below_threshold = confidence is None or confidence < threshold.review_below
    ocr_status = (
        NormativeDocument.OcrStatus.NEEDS_REVIEW
        if below_threshold
        else NormativeDocument.OcrStatus.INDEXED
    )

    with transaction.atomic():
        document = NormativeDocument.objects.select_for_update().get(pk=document_id)
        if document.files_original.name != source_name:
            return
        if _already_finalized(task_id):
            return
        document.ocr_body = text
        document.ocr_confidence = confidence
        document.ocr_status = ocr_status
        document.save(update_fields=["ocr_body", "ocr_confidence", "ocr_status"])
        sync_ocr_review_queue(
            document.pk,
            needs_review=ocr_status == NormativeDocument.OcrStatus.NEEDS_REVIEW,
        )
        AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_OCR_COMPLETED,
            object_type="NormativeDocument",
            object_id=str(document.pk),
            details={
                "text_length": len(text),
                "confidence": confidence,
                "review_threshold": threshold.review_below,
                "ocr_status": ocr_status,
                "task_id": task_id,
            },
        )
