from django.db import DatabaseError, connection, transaction
from django.test import TestCase

from .models import AuditLog


class AuditLogWormTests(TestCase):
    """Журнал аудита неизменяем: ни одна запись не должна поддаваться
    изменению или удалению (ТЗ 4.3.1, 4.7) — ни через ORM, ни в обход него."""

    def _create_entry(self, object_id="142-п"):
        return AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_PUBLISHED,
            actor_personnel_number="0001",
            object_type="NormativeDocument",
            object_id=object_id,
        )

    def test_create_succeeds(self):
        entry = self._create_entry()
        self.assertTrue(AuditLog.objects.filter(pk=entry.pk).exists())

    def test_read_after_create_is_not_blocked(self):
        # WORM запрещает изменение и удаление, но не чтение — экспорт по
        # GET /api/v1/audit/export (ТЗ §7) должен работать без ограничений.
        self._create_entry(object_id="142-п")
        self._create_entry(object_id="143-п")
        self.assertEqual(AuditLog.objects.count(), 2)
        self.assertTrue(AuditLog.objects.filter(object_id="142-п").exists())

    def test_instance_save_update_forbidden(self):
        entry = self._create_entry()
        entry.object_id = "143-п"
        with self.assertRaises(PermissionError):
            entry.save()

    def test_instance_delete_forbidden(self):
        entry = self._create_entry()
        with self.assertRaises(PermissionError):
            entry.delete()

    def test_queryset_update_forbidden(self):
        self._create_entry()
        with self.assertRaises(PermissionError):
            AuditLog.objects.all().update(object_id="143-п")

    def test_queryset_delete_forbidden(self):
        self._create_entry()
        with self.assertRaises(PermissionError):
            AuditLog.objects.all().delete()

    def test_bulk_update_forbidden(self):
        # bulk_update() — отдельный от update() метод QuerySet, реализованный
        # через собственный UPDATE-запрос; переопределение update() его не
        # перехватывает, поэтому это отдельная проверка.
        entry = self._create_entry()
        entry.object_id = "143-п"
        with self.assertRaises(PermissionError):
            AuditLog.objects.bulk_update([entry], ["object_id"])

    def test_bulk_create_with_update_conflicts_forbidden(self):
        entry = AuditLog(
            event_type=AuditLog.EventType.DOCUMENT_PUBLISHED,
            object_type="NormativeDocument",
            object_id="142-п",
        )
        with self.assertRaises(PermissionError):
            AuditLog.objects.bulk_create(
                [entry], update_conflicts=True, update_fields=["object_id"], unique_fields=["id"]
            )

    def test_plain_bulk_create_allowed(self):
        # Обычный bulk_create — это только вставка новых записей, WORM не нарушает.
        entries = [
            AuditLog(event_type=AuditLog.EventType.DOCUMENT_PUBLISHED, object_id="142-п"),
            AuditLog(event_type=AuditLog.EventType.DOCUMENT_PUBLISHED, object_id="143-п"),
        ]
        AuditLog.objects.bulk_create(entries)
        self.assertEqual(AuditLog.objects.count(), 2)


class AuditLogDatabaseLevelWormTests(TestCase):
    """Защита должна держаться и при обходе Django ORM — прямым SQL
    (ТЗ 4.7: WORM на уровне БД, не только на уровне приложения)."""

    def test_raw_sql_update_is_rejected_by_db_trigger(self):
        AuditLog.objects.create(event_type=AuditLog.EventType.DOCUMENT_PUBLISHED, object_id="142-п")
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("UPDATE audit_auditlog SET object_id = 'hacked'")

    def test_raw_sql_delete_is_rejected_by_db_trigger(self):
        AuditLog.objects.create(event_type=AuditLog.EventType.DOCUMENT_PUBLISHED, object_id="142-п")
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM audit_auditlog")

    def test_raw_sql_select_still_works(self):
        AuditLog.objects.create(event_type=AuditLog.EventType.DOCUMENT_PUBLISHED, object_id="142-п")
        with connection.cursor() as cursor:
            cursor.execute("SELECT object_id FROM audit_auditlog")
            rows = cursor.fetchall()
        self.assertEqual(rows, [("142-п",)])
