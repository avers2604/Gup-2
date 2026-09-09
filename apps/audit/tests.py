from django.test import TestCase

from .models import AuditLog


class AuditLogWormTests(TestCase):
    """Журнал аудита неизменяем: ни одна запись не должна поддаваться
    изменению или удалению (ТЗ 4.3.1, 4.7)."""

    def _create_entry(self):
        return AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_PUBLISHED,
            actor_personnel_number="0001",
            object_type="NormativeDocument",
            object_id="142-п",
        )

    def test_create_succeeds(self):
        entry = self._create_entry()
        self.assertTrue(AuditLog.objects.filter(pk=entry.pk).exists())

    def test_instance_save_update_forbidden(self):
        entry = self._create_entry()
        entry.object_id = "143-п"
        with self.assertRaises(PermissionError):
            entry.save()

    def test_instance_delete_forbidden(self):
        entry = self._create_entry()
        with self.assertRaises(PermissionError):
            entry.delete()

    def test_queryset_bulk_update_forbidden(self):
        self._create_entry()
        with self.assertRaises(PermissionError):
            AuditLog.objects.all().update(object_id="143-п")

    def test_queryset_bulk_delete_forbidden(self):
        self._create_entry()
        with self.assertRaises(PermissionError):
            AuditLog.objects.all().delete()
