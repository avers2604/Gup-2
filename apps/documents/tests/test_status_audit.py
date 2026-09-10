from django.test import TestCase

from apps.audit.models import AuditLog
from apps.iam.models import Department, User

from ..models import NormativeDocument
from .factories import make_document


class NormativeDocumentStatusChangeAuditTests(TestCase):
    """Усиление аудита (решение Заказчика: «фиксировать все изменения
    документов — кто, что изменил, старый/новый статус»). Тот же паттерн
    save(), что и у retention_category (см. test_retention.py)."""

    def test_creating_document_writes_no_status_audit_entry(self):
        make_document()
        self.assertFalse(
            AuditLog.objects.filter(
                event_type__in=[
                    AuditLog.EventType.DOCUMENT_PUBLISHED,
                    AuditLog.EventType.DOCUMENT_REVOKED,
                    AuditLog.EventType.DOCUMENT_STATUS_CHANGED,
                ]
            ).exists()
        )

    def test_draft_to_active_writes_document_published(self):
        doc = make_document(status=NormativeDocument.Status.DRAFT)
        doc.status = NormativeDocument.Status.ACTIVE
        doc.save()

        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.DOCUMENT_PUBLISHED)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().details, {"old_status": "draft", "new_status": "active"})

    def test_active_to_revoked_writes_document_revoked(self):
        doc = make_document(status=NormativeDocument.Status.ACTIVE)
        doc.status = NormativeDocument.Status.REVOKED
        doc.save()

        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.DOCUMENT_REVOKED)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().details, {"old_status": "active", "new_status": "revoked"})

    def test_other_transition_writes_generic_document_status_changed(self):
        doc = make_document(status=NormativeDocument.Status.ACTIVE)
        doc.status = NormativeDocument.Status.ARCHIVED
        doc.save()

        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.DOCUMENT_STATUS_CHANGED)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().details, {"old_status": "active", "new_status": "archived"})

    def test_resaving_same_status_writes_no_audit_entry(self):
        doc = make_document(status=NormativeDocument.Status.ACTIVE)
        doc.summary = "Обновлённая аннотация"
        doc.save()
        self.assertFalse(
            AuditLog.objects.filter(
                event_type__in=[AuditLog.EventType.DOCUMENT_PUBLISHED, AuditLog.EventType.DOCUMENT_STATUS_CHANGED]
            ).exists()
        )

    def test_actor_is_captured_from_transient_attribute(self):
        dept, _ = Department.objects.get_or_create(
            name="Служба движения", defaults={"level": Department.Level.SERVICE}
        )
        operator = User.objects.create(
            personnel_number="0099", last_name="Петров", first_name="Иван",
            position="Контролёр", department=dept, role=User.Role.CONTROLLER_LAWYER,
        )
        doc = make_document(status=NormativeDocument.Status.DRAFT)
        doc.status = NormativeDocument.Status.ACTIVE
        doc._audit_actor = operator
        doc.save()

        entry = AuditLog.objects.get(event_type=AuditLog.EventType.DOCUMENT_PUBLISHED)
        self.assertEqual(entry.actor_personnel_number, "0099")
