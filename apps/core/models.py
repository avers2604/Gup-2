import uuid

from django.db import models
from django.utils import timezone


class UUIDPKModel(models.Model):
    """Первичный ключ UUIDv4 — используется карточкой НРД, бланками, аудитом (ТЗ 4.2.1)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BusinessMetricCounter(models.Model):
    """Устойчивый монотонный счётчик бизнес-метрик для Prometheus.

    Счётчики хранятся в PostgreSQL, а не в памяти gunicorn-процесса, поэтому
    не теряются при переключении между несколькими application workers и
    переживают их рестарт. Изменяются только атомарным increment helper.
    """

    name = models.CharField(max_length=80, unique=True)
    value = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name}={self.value}"


class OcrReviewQueueEntry(models.Model):
    """Момент попадания документа в очередь ручной вычитки OCR.

    Отдельная запись нужна, потому что NormativeDocument.updated_at может
    меняться по несвязанным причинам и не должна обнулять возраст просрочки.
    """

    document_id = models.UUIDField(unique=True)
    required_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["required_at"], name="core_ocrrev_require_4d2cbf_idx"),
        ]
        ordering = ["required_at"]


class QueueDrillProbe(models.Model):
    """DB-evidence для стендовой проверки Celery/Redis redelivery.

    delivery_count показывает, сколько раз сообщение реально вошло в worker
    (повтор после worker loss допустим), completion_count — сколько раз был
    зафиксирован бизнес-эффект. Идемпотентный task обязан оставить ровно 1.
    """

    run_id = models.CharField(max_length=80)
    sequence = models.PositiveIntegerField()
    task_id = models.CharField(max_length=255, blank=True)
    delivery_count = models.PositiveIntegerField(default=0)
    completion_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run_id", "sequence"], name="unique_queue_drill_probe"),
        ]
        indexes = [
            models.Index(fields=["run_id", "completed_at"], name="core_queue_run_id_9f8bd3_idx"),
        ]
        ordering = ["run_id", "sequence"]

    def __str__(self):
        return f"{self.run_id}:{self.sequence} ({self.completion_count})"


class TaskOutbox(models.Model):
    """Durable broker delivery intent, committed with the source mutation."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_name = models.CharField(max_length=200)
    args = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    delivered_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)

    class Meta:
        indexes = [models.Index(fields=["delivered_at", "next_attempt_at"], name="outbox_pending_idx")]
