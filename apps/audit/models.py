import uuid

from django.conf import settings
from django.db import models


class WORMQuerySet(models.QuerySet):
    """Запрет массовых update/delete — журнал аудита неизменяем (ТЗ 4.7)."""

    def update(self, **kwargs):
        raise PermissionError("Записи журнала аудита WORM неизменяемы: update() запрещён.")

    def delete(self):
        raise PermissionError("Записи журнала аудита WORM неизменяемы: delete() запрещён.")


class AuditLog(models.Model):
    """Неизменяемый (WORM) журнал аудита: публикация, отмена, замена формы,
    смена статусов, операции выгрузки (ТЗ 4.3.1, 4.7; дополнение к ТЗ п.2.2-2.3
    фиксирует состав событий как стабильный контракт для будущего экспорта в Syslog/CEF)."""

    class EventType(models.TextChoices):
        DOCUMENT_PUBLISHED = "document.published", "Документ опубликован"
        DOCUMENT_REVOKED = "document.revoked", "Документ отменён"
        TEMPLATE_UPDATED = "template.updated", "Бланк обновлён (минорно)"
        TEMPLATE_SUPERSEDED = "template.superseded", "Бланк заменён новой редакцией"
        SESSION_LOGIN = "session.login", "Вход в систему"
        SESSION_LOGOUT = "session.logout", "Выход из системы"
        EXPORT_RESTRICTED = "export.restricted", "Выгрузка документа «ДСП»"
        ARCHIVE_DOWNLOAD = "archive.download", "Скачивание архивного бланка"

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
        ]

    def __str__(self):
        return f"{self.get_event_type_display()} · {self.created_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        if self.pk and AuditLog.objects.filter(pk=self.pk).exists():
            raise PermissionError("Записи журнала аудита WORM неизменяемы: изменение существующей записи запрещено.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Записи журнала аудита WORM неизменяемы: удаление запрещено.")
