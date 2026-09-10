"""Celery-задача конвейера OCR (Этап 3). Запускается из
NormativeDocument.save() при смене files_original (см. models.py) через
transaction.on_commit(), чтобы не читать ещё не закоммиченный файл из
отдельного соединения воркера.
"""
import logging

from celery import shared_task

from apps.audit.models import AuditLog

from .ocr import extract_text_and_confidence

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def run_ocr_for_document(self, document_id):
    from .models import NormativeDocument  # избегаем цикла импорта models<->tasks при старте приложения

    try:
        document = NormativeDocument.objects.get(pk=document_id)
    except NormativeDocument.DoesNotExist:
        # Карточка удалена/не дошла до БД между enqueue и запуском задачи —
        # ретраить нечего, это не сбой распознавания.
        logger.warning("run_ocr_for_document: документ %s не найден, задача пропущена", document_id)
        return

    try:
        with document.files_original.open("rb") as fh:
            pdf_bytes = fh.read()
        text, confidence = extract_text_and_confidence(pdf_bytes)
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        # Retry исчерпан — одна финальная запись в WORM-журнал (не на
        # каждую промежуточную попытку, см. AuditLog.EventType.DOCUMENT_OCR_FAILED).
        AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_OCR_FAILED,
            object_type="NormativeDocument",
            object_id=document.reg_number,
            details={"error": str(exc), "retries": self.request.retries},
        )
        return

    # .update(), не .save(): системная фоновая запись результата — не
    # изменение files_original (не должна сама себя повторно поставить в
    # очередь) и не относится к тем статусным полям, что вызывают
    # обязательный аудит смены статуса/категории в NormativeDocument.save().
    NormativeDocument.objects.filter(pk=document_id).update(
        ocr_body=text, ocr_confidence=confidence,
    )
    AuditLog.objects.create(
        event_type=AuditLog.EventType.DOCUMENT_OCR_COMPLETED,
        object_type="NormativeDocument",
        object_id=document.reg_number,
        details={"text_length": len(text), "confidence": confidence},
    )
