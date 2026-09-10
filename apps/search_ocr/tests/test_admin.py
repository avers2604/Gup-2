from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase

from apps.audit.models import AuditLog
from apps.iam.models import Department, User

from ..admin import ThesaurusEntryAdmin
from ..models import ThesaurusCategory, ThesaurusEntry, ThesaurusStatus


def _make_user(role, personnel_number="0001"):
    dept, _ = Department.objects.get_or_create(
        name="Служба движения", defaults={"level": Department.Level.SERVICE}
    )
    return User.objects.create(
        personnel_number=personnel_number, last_name="Иванов", first_name="Пётр",
        position="Контролёр", department=dept, role=role,
    )


def _make_entry(entry_id="term", status=ThesaurusStatus.DRAFT):
    return ThesaurusEntry.objects.create(
        id=entry_id, canonical=f"Термин {entry_id}", category=ThesaurusCategory.PROCESS,
        short_forms=["ТТ"], status=status,
    )


class ThesaurusAdminCurationPermissionTests(TestCase):
    """cb0713a добавил проверку роли на mark_verified/mark_rejected —
    здесь проверяем, что она реально блокирует «Читателя» и реально
    пропускает «Контролёра/Юриста» (роль, унаследовавшая функционал
    упразднённого «Куратора службы», см. apps/iam/models.py), а не просто
    существует как мёртвый код."""

    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = ThesaurusEntryAdmin(ThesaurusEntry, self.site)

    def _request_as(self, user):
        request = self.factory.post("/admin/search_ocr/thesaurusentry/")
        request.user = user
        request._messages = _NullMessageStorage()
        return request

    def test_reader_cannot_verify(self):
        entry = _make_entry()
        reader = _make_user(User.Role.READER)
        self.admin.mark_verified(self._request_as(reader), ThesaurusEntry.objects.filter(pk=entry.pk))
        entry.refresh_from_db()
        self.assertEqual(entry.status, ThesaurusStatus.DRAFT)

    def test_controller_lawyer_can_verify(self):
        entry = _make_entry()
        controller = _make_user(User.Role.CONTROLLER_LAWYER)
        self.admin.mark_verified(self._request_as(controller), ThesaurusEntry.objects.filter(pk=entry.pk))
        entry.refresh_from_db()
        self.assertEqual(entry.status, ThesaurusStatus.VERIFIED)

    def test_administrator_can_reject(self):
        entry = _make_entry()
        admin_user = _make_user(User.Role.ADMINISTRATOR)
        self.admin.mark_rejected(self._request_as(admin_user), ThesaurusEntry.objects.filter(pk=entry.pk))
        entry.refresh_from_db()
        self.assertEqual(entry.status, ThesaurusStatus.REJECTED)


class ThesaurusAdminCurationAuditTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = ThesaurusEntryAdmin(ThesaurusEntry, self.site)

    def _request_as(self, user):
        request = self.factory.post("/admin/search_ocr/thesaurusentry/")
        request.user = user
        request._messages = _NullMessageStorage()
        return request

    def test_verify_writes_audit_entry_with_operator_and_diff(self):
        entry = _make_entry()
        controller = _make_user(User.Role.CONTROLLER_LAWYER, personnel_number="0042")
        self.admin.mark_verified(self._request_as(controller), ThesaurusEntry.objects.filter(pk=entry.pk))

        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.THESAURUS_UPDATED)
        self.assertEqual(entries.count(), 1)
        log = entries.first()
        self.assertEqual(log.actor_personnel_number, "0042")
        self.assertEqual(log.details["changes"][entry.pk], ["draft", "verified"])

    def test_denied_action_writes_no_audit_entry(self):
        entry = _make_entry()
        reader = _make_user(User.Role.READER)
        self.admin.mark_verified(self._request_as(reader), ThesaurusEntry.objects.filter(pk=entry.pk))
        self.assertFalse(
            AuditLog.objects.filter(event_type=AuditLog.EventType.THESAURUS_UPDATED).exists()
        )

    def test_verifying_already_verified_entry_writes_no_audit_entry(self):
        # Идемпотентность действия: повторный клик "подтвердить" на уже
        # подтверждённой записи не должен создавать шум в WORM-журнале.
        entry = _make_entry(status=ThesaurusStatus.VERIFIED)
        controller = _make_user(User.Role.CONTROLLER_LAWYER)
        self.admin.mark_verified(self._request_as(controller), ThesaurusEntry.objects.filter(pk=entry.pk))
        self.assertFalse(
            AuditLog.objects.filter(event_type=AuditLog.EventType.THESAURUS_UPDATED).exists()
        )


class _NullMessageStorage:
    """Минимальная заглушка django.contrib.messages для admin-действий в
    тестах без полного middleware-стека — self.message_user() пишет сюда."""

    def add(self, level, message, extra_tags=""):
        pass
