from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models, transaction

from apps.core.models import TimeStampedModel, UUIDPKModel
from apps.core.storage import originals_storage, working_storage
from apps.iam.models import Department

from .retention import (
    NORMATIVE_DOCUMENT_CATEGORIES,
    RETENTION_MATRIX,
    RetentionCategory,
    RetentionMode,
    is_expired_at_intake,
    resolve_retention_until,
)


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

    files_original = models.FileField(
        upload_to="documents/originals/%Y/%m/", storage=originals_storage,
        verbose_name="Скан оригинала (PDF/A)",
    )
    files_original_sha256 = models.CharField(max_length=64, blank=True, verbose_name="SHA-256 оригинала")
    files_editable = models.FileField(
        upload_to="documents/editable/%Y/%m/", storage=working_storage,
        blank=True, null=True, verbose_name="Редактируемый файл",
    )

    ocr_confidence = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name="OCR Confidence Score",
    )

    # Срок хранения и режим Object Locking (WORM) — apps/documents/retention.py.
    # Категория указывается человеком при регистрации (юридическая
    # классификация, не техническая эвристика); mode/until считаются
    # автоматически из категории в save(), их нельзя выставить вручную.
    retention_category = models.CharField(
        max_length=32, choices=RetentionCategory.choices, verbose_name="Категория срока хранения",
    )
    retention_mode = models.CharField(
        max_length=16, choices=RetentionMode.choices, editable=False, verbose_name="Режим WORM",
    )
    retention_until = models.DateField(
        null=True, blank=True, editable=False, verbose_name="Хранить до (NULL = бессрочно/не определено)",
    )
    declassification_date = models.DateField(
        null=True, blank=True, verbose_name="Дата рассекречивания",
        help_text="Только для документов ДСП — срок хранения отсчитывается от неё, не от reg_date.",
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

    def clean(self):
        super().clean()
        if self.retention_category and self.retention_category not in NORMATIVE_DOCUMENT_CATEGORIES:
            raise ValidationError({
                "retention_category": (
                    "Эта категория срока хранения неприменима к карточке НРД "
                    "(предназначена для бланков или ещё не реализованной модели актов)."
                ),
            })

    def save(self, *args, **kwargs):
        # retention_until — юридическая дата, а не производное поле, которое
        # можно пересчитывать на каждый save(): если бы она пересчитывалась
        # безусловно (как раньше), достаточно было бы просто сохранить
        # карточку ещё раз спустя время, чтобы дата "уехала" от исходной
        # даты присвоения категории вперёд/назад — WORM-хранение подразумевает
        # фиксированный срок, а не плавающий. Поэтому пересчёт происходит
        # только при первом сохранении и при фактической смене
        # retention_category; смена категории — это изменение юридической
        # классификации и обязана попасть в WORM-журнал аудита (старое и
        # новое значение), а не пройти тихо.
        previous = (
            type(self).objects.filter(pk=self.pk).values_list("retention_category", "status").first()
        )
        previous_category = previous[0] if previous is not None else None
        previous_status = previous[1] if previous is not None else None
        is_new = previous is None
        category_changed = is_new or previous_category != self.retention_category
        # Усиление аудита (решение Заказчика: «фиксировать все изменения
        # документов — кто, что изменил, старый/новый статус»). Только на
        # реальном изменении (не на первом сохранении — создание карточки
        # не «изменение статуса», это его первое присвоение).
        status_changed = not is_new and previous_status != self.status

        if self.retention_category and category_changed:
            policy = RETENTION_MATRIX[self.retention_category]
            self.retention_mode = policy.mode
            self.retention_until = resolve_retention_until(
                self.retention_category,
                reg_date=self.reg_date,
                declassification_date=self.declassification_date,
            )
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                    "retention_mode", "retention_until",
                }

        with transaction.atomic():
            super().save(*args, **kwargs)

            if category_changed:
                if is_new:
                    # Обратная загрузка старого документа с уже истёкшим по матрице
                    # сроком хранения — не ошибка данных (см. retention.is_expired_at_intake),
                    # но и не то, что должно пройти незамеченным: пишем
                    # предупреждающую запись в журнал аудита.
                    if is_expired_at_intake(self.retention_until):
                        from apps.audit.models import AuditLog

                        AuditLog.objects.create(
                            event_type=AuditLog.EventType.DOCUMENT_RETENTION_EXPIRED_AT_INTAKE,
                            object_type="NormativeDocument",
                            object_id=self.reg_number,
                            details={
                                "retention_category": self.retention_category,
                                "retention_until": self.retention_until.isoformat(),
                            },
                        )
                else:
                    from apps.audit.models import AuditLog

                    AuditLog.objects.create(
                        event_type=AuditLog.EventType.DOCUMENT_RETENTION_CATEGORY_CHANGED,
                        object_type="NormativeDocument",
                        object_id=self.reg_number,
                        details={
                            "old_category": previous_category,
                            "new_category": self.retention_category,
                        },
                    )

            if status_changed:
                # Усиление аудита (решение Заказчика). Событие выбирается по
                # НОВОМУ статусу — только два самых однозначных случая
                # получают уже существовавшие (ранее ни разу не написанные)
                # специализированные типы DOCUMENT_PUBLISHED/DOCUMENT_REVOKED;
                # любой другой переход (DRAFT -> ACTIVE_AMENDED, ACTIVE ->
                # ARCHIVED и т.д.) — новый общий DOCUMENT_STATUS_CHANGED, а
                # не досочинённая под него семантика специализированных типов.
                from apps.audit.models import AuditLog

                event_type = {
                    self.Status.ACTIVE: AuditLog.EventType.DOCUMENT_PUBLISHED,
                    self.Status.REVOKED: AuditLog.EventType.DOCUMENT_REVOKED,
                }.get(self.status, AuditLog.EventType.DOCUMENT_STATUS_CHANGED)

                actor = getattr(self, "_audit_actor", None)
                AuditLog.objects.create(
                    event_type=event_type,
                    actor=actor,
                    actor_personnel_number=getattr(actor, "personnel_number", ""),
                    object_type="NormativeDocument",
                    object_id=self.reg_number,
                    details={"old_status": previous_status, "new_status": self.status},
                )


class DocumentRelationQuerySet(models.QuerySet):
    """bulk_create() запрещён — обнаружение циклов длиной больше одного
    ребра (apps.documents.services.relation_would_create_cycle) требует
    вставки по одной связи за раз через .save()/.create(), которые вызывают
    full_clean(). bulk_create() обходит save() целиком, а проверять цикл
    для каждого объекта партии независимо недостаточно: цикл может
    замыкаться связями ВНУТРИ одного пакета (A->B и B->A в одном вызове),
    и последовательная проверка «текущий объект против уже сохранённого в
    БД графа» его не увидит, если обе стороны ещё не закоммичены. Честнее
    запретить путь целиком, чем сделать вид, что он безопасен.

    На будущее (Этап 4, массовая загрузка исторического фонда связей при
    импорте архива): построчная проверка через .save() станет медленной на
    больших объёмах. Правильное развитие — не снимать этот запрет, а
    добавить ОТДЕЛЬНЫЙ пакетный метод с проверкой ацикличности всего
    пакета рёбер целиком одним рекурсивным CTE (добавить рёбра пакета во
    временную таблицу/CTE и проверить граф с ними разом, а не по одному
    ребру против уже закоммиченного графа)."""

    def bulk_create(self, objs, *args, **kwargs):
        raise NotImplementedError(
            "DocumentRelation.objects.bulk_create() запрещён — проверка ацикличности "
            "требует создания связей по одной через .save()/.create(). Массовое создание "
            "связей выполняется через сервисный слой (apps/documents/services.py), "
            "а не напрямую через QuerySet."
        )


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

    objects = DocumentRelationQuerySet.as_manager()

    class Meta:
        verbose_name = "Связь версионности"
        verbose_name_plural = "Связи версионности"
        constraints = [
            models.UniqueConstraint(
                fields=["from_document", "to_document", "relation_type"],
                name="unique_document_relation",
            ),
            models.CheckConstraint(
                condition=~models.Q(from_document=models.F("to_document")),
                name="document_relation_no_self_reference",
            ),
        ]

    def __str__(self):
        return f"{self.from_document.reg_number} → {self.get_relation_type_display()} → {self.to_document.reg_number}"

    def clean(self):
        super().clean()
        # Самоссылку и точный дубль уже ловят CheckConstraint/UniqueConstraint
        # в Meta (это защита на уровне БД, независимая от Python). Цикл
        # длиной больше единицы (A -> B -> C -> A) они физически не видят —
        # SQL CHECK смотрит только на вставляемую строку, не на весь граф.
        if self.from_document_id and self.to_document_id:
            from .services import relation_would_create_cycle

            if relation_would_create_cycle(self.from_document_id, self.to_document_id):
                raise ValidationError(
                    "Эта связь создаст цикл в графе версионности "
                    f"({self.from_document_id} -> ... -> {self.to_document_id} уже существует)."
                )

    def save(self, *args, **kwargs):
        document_ids = {self.from_document_id, self.to_document_id} - {None}
        with transaction.atomic():
            if document_ids:
                list(
                    NormativeDocument.objects.select_for_update()
                    .filter(pk__in=document_ids)
                    .order_by("pk")
                )
            self.full_clean()
            super().save(*args, **kwargs)


class DocumentStatusHistory(models.Model):
    """Темпоральные срезы SCD-2 (ТЗ 4.2.3): tstzrange + EXCLUDE USING gist,
    исключающий пересечение периодов действия одного документа.

    Гонка транзакций: EXCLUDE USING gist защищает целостность данных на
    уровне БД — две параллельные транзакции никогда не закоммитят
    пересекающиеся периоды одновременно, одна из них гарантированно
    получит IntegrityError (см. ConcurrentStatusTransitionTests). Но сам
    по себе constraint не даёт "плавной" семантики перехода: сервис,
    который будет закрывать текущий период (valid_to) и открывать новый
    при публикации документа (Этап 2, пока не реализован), должен
    блокировать строку NormativeDocument через
    `NormativeDocument.objects.select_for_update()` на время обеих
    операций — иначе конкурентный переход просто упадёт с ошибкой вместо
    корректной последовательной обработки, и вызывающему коду нужно будет
    самому решать, ретраить или нет."""

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

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.document_id:
                NormativeDocument.objects.select_for_update().get(pk=self.document_id)
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.document.reg_number}: {self.status} {self.period}"
