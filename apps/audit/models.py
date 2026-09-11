import uuid

from django.conf import settings
from django.db import models


class WORMQuerySet(models.QuerySet):
    """Запрет массовых update/delete — журнал аудита неизменяем (ТЗ 4.7).

    bulk_update() перечислен отдельно от update(), потому что Django не
    выражает его через QuerySet.update() — это отдельный метод, который
    сам строит и выполняет UPDATE-запрос, и переопределение update() его
    не перехватывает. bulk_create(update_conflicts=True) — по той же
    причине: это INSERT ... ON CONFLICT DO UPDATE, то есть замаскированный
    update через видимость INSERT. Обычный bulk_create() (без
    update_conflicts) — это просто пакетная вставка новых записей и WORM
    не нарушает, поэтому разрешён."""

    def update(self, **kwargs):
        raise PermissionError("Записи журнала аудита WORM неизменяемы: update() запрещён.")

    def delete(self):
        raise PermissionError("Записи журнала аудита WORM неизменяемы: delete() запрещён.")

    def bulk_update(self, objs, fields, **kwargs):
        raise PermissionError("Записи журнала аудита WORM неизменяемы: bulk_update() запрещён.")

    def bulk_create(self, objs, *args, **kwargs):
        if kwargs.get("update_conflicts"):
            raise PermissionError(
                "Записи журнала аудита WORM неизменяемы: "
                "bulk_create(update_conflicts=True) запрещён."
            )
        return super().bulk_create(objs, *args, **kwargs)


class AuditLog(models.Model):
    """Неизменяемый (WORM) журнал аудита: публикация, отмена, замена формы,
    смена статусов, операции выгрузки (ТЗ 4.3.1, 4.7; дополнение к ТЗ п.2.2-2.3
    фиксирует состав событий как стабильный контракт для будущего экспорта в Syslog/CEF)."""

    class EventType(models.TextChoices):
        DOCUMENT_PUBLISHED = "document.published", "Документ опубликован"
        DOCUMENT_REVOKED = "document.revoked", "Документ отменён"
        # Любая другая смена status карточки НРД, не подпадающая под
        # «опубликован»/«отменён» буквально (например draft -> active_amended,
        # active -> archived) — усиление аудита (решение Заказчика: «фиксировать
        # все изменения документов — старый/новый статус»), см. NormativeDocument.save().
        DOCUMENT_STATUS_CHANGED = "document.status_changed", "Изменён статус документа"
        # Исправление ошибки публикации (решение Заказчика). Оба перехода
        # доступны только Администратору и требуют основания, поэтому у
        # них собственные типы событий, а не общий STATUS_CHANGED: это
        # ровно те записи, которые проверяющий ищет в журнале в первую
        # очередь, и находить их фильтром по «изменён статус» вперемешку
        # с рутинными переходами — значит не находить.
        DOCUMENT_PUBLICATION_ROLLED_BACK = (
            "document.publication_rolled_back", "Публикация откачена в черновик"
        )
        DOCUMENT_ANNULLED = "document.annulled", "Публикация аннулирована"
        TEMPLATE_UPDATED = "template.updated", "Бланк обновлён (минорно)"
        TEMPLATE_SUPERSEDED = "template.superseded", "Бланк заменён новой редакцией"
        SESSION_LOGIN = "session.login", "Вход в систему"
        SESSION_LOGOUT = "session.logout", "Выход из системы"
        # Неудачная попытка входа (неверный пароль/код TOTP/заблокированный
        # пользователь/просроченный тикет) — усиление аудита (решение
        # Заказчика, основа для Grafana-алерта «5+ неудачных попыток подряд»).
        SESSION_LOGIN_FAILED = "session.login_failed", "Неудачная попытка входа"
        EXPORT_RESTRICTED = "export.restricted", "Выгрузка документа «ДСП»"
        ARCHIVE_DOWNLOAD = "archive.download", "Скачивание архивного бланка"
        USER_ROLE_ELEVATED = "user.role_elevated", "Повышение роли пользователя"
        # Комплексный аудит ЛЮБОГО изменения роли (не только повышения) —
        # усиление аудита (решение Заказчика). Пишется из User.save(), в
        # дополнение к более узкому USER_ROLE_ELEVATED (см. его docstring и
        # STACK.md про намеренное пересечение событий).
        USER_ROLE_CHANGED = "user.role_changed", "Изменение роли пользователя"
        # Административный сброс 2FA (по запросу ревью анти-фрода) —
        # единственный путь восстановить доступ пользователю, потерявшему
        # устройство-аутентификатор (до этой партии такого сценария в
        # проекте не было вовсе, см. STACK.md). Пишется ДО сохранения
        # сброшенных полей — тот же порядок, что у UPLOAD_MALWARE_DETECTED.
        USER_TOTP_RESET = "user.totp_reset", "Администратор сбросил 2FA (TOTP)"
        DOCUMENT_RETENTION_CATEGORY_CHANGED = (
            "document.retention_category_changed", "Изменена категория срока хранения"
        )
        DOCUMENT_RETENTION_EXPIRED_AT_INTAKE = (
            "document.retention_expired_at_intake", "Срок хранения уже истёк на момент регистрации"
        )
        # Рёбра графа версионности (ТЗ 4.2.2). Сама таблица связей не
        # WORM — ребро можно снять, если его завели по ошибке. Именно
        # поэтому оба события и заведены: снятие связи меняет граф, по
        # которому документ считается отменённым или изменённым, и не
        # должно быть бесследным.
        DOCUMENT_RELATION_ADDED = "document.relation_added", "Добавлена связь версионности"
        DOCUMENT_RELATION_REMOVED = "document.relation_removed", "Снята связь версионности"
        THESAURUS_UPDATED = "thesaurus.updated", "Обновление тезауруса (импорт)"
        DOCUMENT_OCR_COMPLETED = "document.ocr_completed", "Распознавание OCR завершено"
        # Финальный отказ после исчерпания retry (apps/documents/tasks.py) —
        # не пишется на каждую промежуточную попытку, иначе один неудачный
        # документ дал бы несколько записей в WORM-журнале на одно событие.
        DOCUMENT_OCR_FAILED = "document.ocr_failed", "Распознавание OCR не выполнено"
        # Ручная вычитка распознанного текста (рабочее место «Вычитка OCR»).
        # Пишется отдельно от DOCUMENT_OCR_COMPLETED: там результат машины,
        # здесь — правка человека, и для разбора «почему документ ищется
        # именно так» важно различать, кто именно сформировал ocr_body.
        DOCUMENT_OCR_REVIEWED = "document.ocr_reviewed", "Вычитка OCR выполнена"
        # Анти-фрод (ТЗ 4.7) — обнаружение сигнатуры антивирусом при
        # загрузке файла НРД/бланка (apps/core/antivirus.py). Пишется ДО
        # отказа save() — файл не попадает в хранилище, событие в
        # WORM-журнале остаётся единственным следом попытки загрузки.
        UPLOAD_MALWARE_DETECTED = "upload.malware_detected", "Обнаружен вредоносный файл при загрузке"
        # Структурная проверка на макросы/ActiveX (apps/core/macro_check.py)
        # — дополняет UPLOAD_MALWARE_DETECTED: ClamAV ловит ИЗВЕСТНЫЕ
        # вредоносные макросы по сигнатурам, эта проверка — сам факт
        # наличия VBA-кода в OOXML-документе, независимо от того, знает ли
        # о нём антивирус.
        UPLOAD_MACRO_REJECTED = "upload.macro_rejected", "Отклонён файл с макросами/ActiveX"
        # «Заготовка» — как EXPORT_RESTRICTED/ARCHIVE_DOWNLOAD исторически:
        # событие заведено под будущий Grafana-алерт «экспорт журнала аудита»
        # (решение Заказчика), но в этой партии не пишется НИКАКИМ кодом —
        # экспорта самого журнала аудита в проекте ещё нет ни в одном контуре
        # (честная граница, см. STACK.md).
        AUDIT_LOG_EXPORTED = "audit_log.exported", "Экспорт журнала аудита"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=64, choices=EventType.choices)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    actor_personnel_number = models.CharField(
        max_length=32, blank=True,
        verbose_name="Табельный номер оператора (снимок на момент события)",
    )

    object_type = models.CharField(max_length=64, blank=True, verbose_name="Тип объекта")
    object_id = models.CharField(max_length=64, blank=True, verbose_name="Идентификатор объекта")

    details = models.JSONField(default=dict, blank=True, verbose_name="Реквизиты события")

    created_at = models.DateTimeField(auto_now_add=True)

    objects = WORMQuerySet.as_manager()

    class Meta:
        verbose_name = "Запись журнала аудита"
        verbose_name_plural = "Журнал аудита (WORM)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event_type", "created_at"]),
            models.Index(fields=["object_type", "object_id"]),
            # Скользящее окно rate limiting/lockout (apps/iam/services.py,
            # is_locked_out): COUNT(*) по event_type=SESSION_LOGIN_FAILED +
            # actor_personnel_number + created_at на каждую попытку входа.
            # Равенства первыми, диапазон последним — стандартный порядок
            # колонок составного B-tree индекса под такой запрос.
            models.Index(
                fields=["event_type", "actor_personnel_number", "created_at"],
                name="audit_lockout_window_idx",
            ),
        ]

    def __str__(self):
        return f"{self.get_event_type_display()} · {self.created_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        if self.pk and AuditLog.objects.filter(pk=self.pk).exists():
            raise PermissionError("Записи журнала аудита WORM неизменяемы: изменение существующей записи запрещено.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Записи журнала аудита WORM неизменяемы: удаление запрещено.")
