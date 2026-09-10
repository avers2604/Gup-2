"""Celery-задача конвейера OCR (Этап 3). Запускается из
NormativeDocument.save() при смене files_original (см. models.py) через
transaction.on_commit(), чтобы не читать ещё не закоммиченный файл из
отдельного соединения воркера.
"""
import logging

from celery import shared_task

from apps.audit.models import AuditLog

from .ocr import extract_text_and_confidence
from .ocr_thresholds import get_threshold_for_document

logger = logging.getLogger(__name__)

# Лимиты ТЗ 1.2 §4.2.1 на скан-оригинал (до 150 МБ) означают документы на
# сотни страниц — жёсткий/мягкий таймаут не дают одной зависшей/медленной
# задаче занимать воркер бесконечно. На мягком таймауте Celery поднимает
# SoftTimeLimitExceeded ВНУТРИ тела задачи — тот же except Exception ниже
# его ловит и уходит в обычный путь retry/финального отказа, отдельного
# сохранения частичного результата на мягком таймауте нет (честная граница).
TASK_TIME_LIMIT = 600
TASK_SOFT_TIME_LIMIT = 540


@shared_task(
    bind=True, max_retries=3, default_retry_delay=60,
    time_limit=TASK_TIME_LIMIT, soft_time_limit=TASK_SOFT_TIME_LIMIT,
)
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
        # Документ без единого распознанного символа однозначно требует
        # ручного внимания — тот же статус, что и у документа с низкой
        # уверенностью, а не отдельное "необработан".
        NormativeDocument.objects.filter(pk=document_id).update(
            ocr_status=NormativeDocument.OcrStatus.NEEDS_REVIEW,
        )
        AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_OCR_FAILED,
            object_type="NormativeDocument",
            object_id=document.reg_number,
            details={"error": str(exc), "retries": self.request.retries},
        )
        return

    # Дифференцированные пороги качества по категории документа (ТЗ 2.2
    # §4.4.2, apps/documents/ocr_thresholds.py). confidence=None (ни одного
    # распознанного слова) — заведомо ниже любого порога.
    threshold = get_threshold_for_document(document)
    below_threshold = confidence is None or confidence < threshold.review_below
    ocr_status = (
        NormativeDocument.OcrStatus.NEEDS_REVIEW if below_threshold
        else NormativeDocument.OcrStatus.INDEXED
    )

    # .update(), не .save(): системная фоновая запись результата — не
    # изменение files_original (не должна сама себя повторно поставить в
    # очередь) и не относится к тем статусным полям, что вызывают
    # обязательный аудит смены статуса/категории в NormativeDocument.save().
    NormativeDocument.objects.filter(pk=document_id).update(
        ocr_body=text, ocr_confidence=confidence, ocr_status=ocr_status,
    )
    AuditLog.objects.create(
        event_type=AuditLog.EventType.DOCUMENT_OCR_COMPLETED,
        object_type="NormativeDocument",
        object_id=document.reg_number,
        details={
            "text_length": len(text),
            "confidence": confidence,
            "review_threshold": threshold.review_below,
            "ocr_status": ocr_status,
        },
    )
