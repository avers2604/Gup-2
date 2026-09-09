from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models

from apps.core.models import TimeStampedModel, UUIDPKModel
from apps.iam.models import Department


class Tag(models.Model):
    """Справочник тематических тегов (ТЗ 4.2.1, category_tags) — например
    «Безопасность движения», «ПТЭ». Заполняется методистами служб на Этапе 1."""

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "Тег"
        verbose_name_plural = "Теги"
        ordering = ["name"]

    def __str__(self):
        return self.name


class NormativeDocument(UUIDPKModel, TimeStampedModel):
    """Карточка нормативно-распорядительного документа — атрибуты по таблице ТЗ 4.2.1."""

    class DocType(models.TextChoices):
        ORDER = "order", "Приказ директора"
        DIRECTIVE = "directive", "Распоряжение"
        REGULATION = "regulation", "Положение"
        STO = "sto", "СТО"
        INSTRUCTION = "instruction", "Инструкция"
        PROCEDURE = "procedure", "Регламент"

    class AccessLevel(models.TextChoices):
        GENERAL = "general", "Общий"
        RESTRICTED = "restricted", "ДСП"

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        ACTIVE = "active", "Действует"
        ACTIVE_AMENDED = "active_amended", "Действует с изм."
        REVOKED = "revoked", "Утратил силу"
        ARCHIVED = "archived", "Архив"

    reg_number = models.CharField(max_length=64, verbose_name="Регистрационный номер")
    reg_date = models.DateField(verbose_name="Дата регистрации")
    effective_date = models.DateField(verbose_name="Дата вступления в силу")
    doc_type = models.CharField(max_length=32, choices=DocType.choices, verbose_name="Вид документа")
    title = models.CharField(max_length=500, verbose_name="Наименование")
    summary = models.TextField(blank=True, verbose_name="Аннотация")

    issuer_dept = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="issued_documents", verbose_name="Служба-эмитент"
    )
    applied_depts = models.ManyToManyField(
        Department, blank=True, related_name="applicable_documents", verbose_name="Подразделения применения"
    )
    category_tags = models.ManyToManyField(Tag, blank=True, related_name="documents", verbose_name="Теги")

    access_level = models.CharField(
        max_length=16, choices=AccessLevel.choices, default=AccessLevel.GENERAL, verbose_name="Уровень доступа"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    files_original = models.FileField(upload_to="documents/originals/%Y/%m/", verbose_name="Скан оригинала (PDF/A)")
    files_original_sha256 = models.CharField(max_length=64, blank=True, verbose_name="SHA-256 оригинала")
    files_editable = models.FileField(
        upload_to="documents/editable/%Y/%m/", blank=True, null=True, verbose_name="Редактируемый файл"
    )

    ocr_confidence = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name="OCR Confidence Score",
    )

    class Meta:
        verbose_name = "Нормативно-распорядительный документ"
        verbose_name_plural = "Нормативно-распорядительные документы"
        ordering = ["-reg_date"]
        indexes = [
            models.Index(fields=["reg_number"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.reg_number} — {self.title}"


class DocumentRelation(models.Model):
    """Ориентированный ациклический граф связей версионности (ТЗ 4.2.2)."""

    class RelationType(models.TextChoices):
        CANCELS = "cancels", "Отменяет"
        AMENDS = "amends", "Вносит изменения в"
        REPLACES = "replaces", "Принят взамен"
        APPROVES_TEMPLATE = "approves_template", "Утверждает форму"
        REFERENCES = "references", "Ссылается на"

    from_document = models.ForeignKey(
        NormativeDocument, on_delete=models.CASCADE, related_name="relations_from"
    )
    to_document = models.ForeignKey(
        NormativeDocument, on_delete=models.CASCADE, related_name="relations_to"
    )
    relation_type = models.CharField(max_length=32, choices=RelationType.choices)
    note = models.TextField(blank=True, verbose_name="Описание затронутых пунктов")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Связь версионности"
        verbose_name_plural = "Связи версионности"
        constraints = [
            models.UniqueConstraint(
                fields=["from_document", "to_document", "relation_type"],
                name="unique_document_relation",
            ),
            models.CheckConstraint(
                check=~models.Q(from_document=models.F("to_document")),
                name="document_relation_no_self_reference",
            ),
        ]

    def __str__(self):
        return f"{self.from_document.reg_number} → {self.get_relation_type_display()} → {self.to_document.reg_number}"


class DocumentStatusHistory(models.Model):
    """Темпоральные срезы SCD-2 (ТЗ 4.2.3): tstzrange + EXCLUDE USING gist,
    исключающий пересечение периодов действия одного документа."""

    document = models.ForeignKey(
        NormativeDocument, on_delete=models.CASCADE, related_name="status_history"
    )
    status = models.CharField(max_length=16, choices=NormativeDocument.Status.choices)
    period = DateTimeRangeField(verbose_name="Период действия статуса (valid_from, valid_to)")

    class Meta:
        verbose_name = "Историческая запись статуса"
        verbose_name_plural = "История статусов (SCD-2)"
        ordering = ["document", "period"]
        constraints = [
            ExclusionConstraint(
                name="exclude_overlapping_status_periods",
                expressions=[
                    ("document", RangeOperators.EQUAL),
                    ("period", RangeOperators.OVERLAPS),
                ],
            ),
        ]

    def __str__(self):
        return f"{self.document.reg_number}: {self.status} {self.period}"
