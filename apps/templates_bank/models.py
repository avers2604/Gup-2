import uuid

from django.db import models, transaction

from apps.core.models import TimeStampedModel
from apps.core.storage import originals_storage
from apps.documents.models import NormativeDocument
from apps.documents.retention import RETENTION_MATRIX, RetentionCategory


class TemplateFamily(models.Model):
    """Единый GUID семейства форм — lineage_root_id из ТЗ 4.3.1
    (например, «Акт схода подвижного состава» вне зависимости от редакции)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, verbose_name="Наименование семейства форм")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Семейство форм"
        verbose_name_plural = "Семейства форм"

    def __str__(self):
        return self.name


class Template(TimeStampedModel):
    """Версия бланка внутри семейства — правила переиздания ТЗ 4.3.1.

    Двухступенчатая модель (решение по итогам ревью — закрывает открытый
    вопрос из STACK.md «Governance-блокировка бланков vs working-бакет»):
    ТЗ 4.3.1 прямо требует инкремента версии ДАЖЕ для минорной
    корректировки (v1.0 -> v1.1) — то есть каждая строка Template с самого
    начала является уже ОПУБЛИКОВАННЫМ, неизменяемым артефактом, а не
    черновиком, который правится на месте. Файлы этой строки поэтому
    хранятся в заблокированном originals (Governance, TEMPLATES_APPROVED
    из retention.RETENTION_MATRIX), а не в working, как было решено раньше.

    Незаблокированная рабочая копия для правки ДО публикации новой версии
    (собственно "минорная корректировка" со стороны Контролёра/Юриста —
    роль, унаследовавшая функционал упразднённого «Куратора службы», см.
    apps/iam/models.py) — это отдельный, пока не реализованный механизм
    черновиков (Этап 2/3, будущий UI), результатом работы которого является
    НОВАЯ строка Template с новым version, а не мутация существующей."""

    class ChangeType(models.TextChoices):
        MAJOR = "major", "Новая редакция"
        MINOR = "minor", "Корректировка"

    class Status(models.TextChoices):
        ACTIVE = "active", "Активна"
        SUPERSEDED = "superseded", "superseded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    family = models.ForeignKey(TemplateFamily, on_delete=models.PROTECT, related_name="templates")
    version = models.CharField(max_length=16, verbose_name="Версия (v1.0, v2.0…)")
    change_type = models.CharField(max_length=16, choices=ChangeType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    previous_template = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    superseded_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="supersedes"
    )

    approving_document = models.ForeignKey(
        NormativeDocument, on_delete=models.PROTECT, related_name="approved_templates",
        verbose_name="Утверждающий приказ",
    )
    revoking_document = models.ForeignKey(
        NormativeDocument, null=True, blank=True, on_delete=models.PROTECT,
        related_name="revoked_templates", verbose_name="Отменяющий приказ",
    )

    # originals_storage (Governance-lock) — эта строка Template уже
    # является опубликованной версией (см. docstring класса), а не
    # черновиком. Минорная корректировка создаёт НОВУЮ строку/версию, а не
    # заменяет файл этой.
    file_editable = models.FileField(
        upload_to="templates/editable/%Y/", storage=originals_storage,
        verbose_name="Защищённый рабочий бланк (.docx/.xlsx)",
    )
    file_sample = models.FileField(
        upload_to="templates/samples/%Y/", storage=originals_storage,
        verbose_name="Эталонный образец заполнения (.pdf)",
    )

    download_count = models.PositiveIntegerField(default=0)
    last_reviewed_at = models.DateField(
        null=True, blank=True, verbose_name="Дата последнего пересмотра"
    )

    # Режим WORM для бланков — всегда Governance (retention.RETENTION_MATRIX,
    # TEMPLATES_APPROVED), не варьируется по экземпляру и потому задан как
    # константа класса, а не поле БД (в отличие от
    # NormativeDocument.retention_mode, который зависит от выбранной
    # человеком категории и потому обязан быть полем).
    RETENTION_MODE = RETENTION_MATRIX[RetentionCategory.TEMPLATES_APPROVED].mode

    class Meta:
        verbose_name = "Бланк / шаблон"
        verbose_name_plural = "Бланки и шаблоны"
        ordering = ["family", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["family", "version"], name="unique_template_version"),
        ]

    def __str__(self):
        return f"{self.family.name} {self.version}"

    def save(self, *args, **kwargs):
        # Усиление аудита (решение Заказчика: «фиксировать все изменения
        # бланков — кто, что изменил, старый/новый статус»). Тот же
        # паттерн "сравнить с БД до super().save()", что и у
        # NormativeDocument.status/retention_category — см. их docstring'и.
        previous_status = (
            type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
        )
        is_new = previous_status is None
        status_changed = not is_new and previous_status != self.status

        with transaction.atomic():
            super().save(*args, **kwargs)

            if not status_changed:
                return

            from apps.audit.models import AuditLog

            # ACTIVE -> SUPERSEDED — единственный реальный переход сейчас
            # (TemplateAdmin.save_model блокирует любое изменение уже
            # опубликованной версии, так что TEMPLATE_UPDATED здесь —
            # честная заготовка на случай будущего механизма минорной
            # корректировки/отката SUPERSEDED -> ACTIVE, а не гарантированно
            # используемая сегодня ветка).
            event_type = (
                AuditLog.EventType.TEMPLATE_SUPERSEDED
                if self.status == self.Status.SUPERSEDED
                else AuditLog.EventType.TEMPLATE_UPDATED
            )
            actor = getattr(self, "_audit_actor", None)
            AuditLog.objects.create(
                event_type=event_type,
                actor=actor,
                actor_personnel_number=getattr(actor, "personnel_number", ""),
                object_type="Template",
                object_id=str(self.pk),
                details={"old_status": previous_status, "new_status": self.status},
            )
