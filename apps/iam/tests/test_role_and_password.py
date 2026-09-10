"""Усиление аудита ролей + парольная политика (решение Заказчика):
apps/iam/models.py — User.save() (аудит смены роли, история паролей),
apps/iam/validators.py — PasswordHistoryValidator."""
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.audit.models import AuditLog

from ..models import Department, PasswordHistoryEntry, User


def _make_user(role=User.Role.READER, personnel_number="0001", **kwargs):
    dept, _ = Department.objects.get_or_create(
        name="Служба движения", defaults={"level": Department.Level.SERVICE}
    )
    defaults = dict(
        personnel_number=personnel_number, last_name="Иванов", first_name="Пётр",
        position="Водитель", department=dept, role=role,
    )
    defaults.update(kwargs)
    user = User(**defaults)
    user.set_password("Sup3r$ecret!Pass1")
    user.save()
    return user


class RoleRemovalTests(TestCase):
    """Роль «Куратор службы» упразднена (решение Заказчика) — не должна
    существовать в choices вообще."""

    def test_curator_not_in_role_choices(self):
        self.assertNotIn("curator", dict(User.Role.choices))

    def test_methodist_role_exists(self):
        self.assertIn("methodist", dict(User.Role.choices))

    def test_methodist_sits_between_reader_and_controller_lawyer(self):
        order = User.ROLE_PRIVILEGE_ORDER
        self.assertLess(order.index(User.Role.READER), order.index(User.Role.METHODIST))
        self.assertLess(order.index(User.Role.METHODIST), order.index(User.Role.CONTROLLER_LAWYER))


class RequiresTotpNarrowingTests(TestCase):
    """2FA сужена решением Заказчика: только Администратор (было
    Администратор/Контролёр-Юрист/Куратор)."""

    def test_administrator_requires_totp(self):
        user = _make_user(User.Role.ADMINISTRATOR)
        self.assertTrue(user.requires_totp)

    def test_controller_lawyer_no_longer_requires_totp(self):
        user = _make_user(User.Role.CONTROLLER_LAWYER)
        self.assertFalse(user.requires_totp)

    def test_security_officer_does_not_require_totp(self):
        user = _make_user(User.Role.SECURITY_OFFICER)
        self.assertFalse(user.requires_totp)

    def test_reader_does_not_require_totp(self):
        user = _make_user(User.Role.READER)
        self.assertFalse(user.requires_totp)


class UserRoleChangeAuditTests(TestCase):
    """Усиление аудита (решение Заказчика): любое реальное изменение
    role — не только повышение — пишется в USER_ROLE_CHANGED."""

    def test_creating_user_writes_no_role_change_entry(self):
        _make_user(User.Role.READER)
        self.assertFalse(
            AuditLog.objects.filter(event_type=AuditLog.EventType.USER_ROLE_CHANGED).exists()
        )

    def test_role_change_writes_audit_entry(self):
        user = _make_user(User.Role.READER)
        user.role = User.Role.CONTROLLER_LAWYER
        user.save()

        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.USER_ROLE_CHANGED)
        self.assertEqual(entries.count(), 1)
        details = entries.first().details
        self.assertEqual(details["previous_role"], "reader")
        self.assertEqual(details["new_role"], "controller_lawyer")
        self.assertEqual(details["target_personnel_number"], "0001")

    def test_role_downgrade_also_writes_audit_entry(self):
        # В отличие от узкого USER_ROLE_ELEVATED (только импорт, только
        # повышение) — комплексный аудит фиксирует ЛЮБОЕ направление.
        user = _make_user(User.Role.SECURITY_OFFICER)
        user.role = User.Role.READER
        user.save()
        self.assertEqual(
            AuditLog.objects.filter(event_type=AuditLog.EventType.USER_ROLE_CHANGED).count(), 1
        )

    def test_resaving_same_role_writes_no_audit_entry(self):
        user = _make_user(User.Role.READER)
        user.position = "Другая должность"
        user.save()
        self.assertFalse(
            AuditLog.objects.filter(event_type=AuditLog.EventType.USER_ROLE_CHANGED).exists()
        )

    def test_audit_actor_captured_from_transient_attribute(self):
        admin_user = _make_user(User.Role.ADMINISTRATOR, personnel_number="0099")
        target = _make_user(User.Role.READER, personnel_number="0001")
        target.role = User.Role.METHODIST
        target._audit_actor = admin_user
        target.save()

        entry = AuditLog.objects.get(event_type=AuditLog.EventType.USER_ROLE_CHANGED)
        self.assertEqual(entry.actor_personnel_number, "0099")

    def test_no_actor_leaves_blank_actor_personnel_number(self):
        user = _make_user(User.Role.READER)
        user.role = User.Role.METHODIST
        user.save()
        entry = AuditLog.objects.get(event_type=AuditLog.EventType.USER_ROLE_CHANGED)
        self.assertEqual(entry.actor_personnel_number, "")


class PasswordHistoryTests(TestCase):
    """Парольная политика (решение Заказчика): история 10 паролей."""

    def test_creating_user_writes_no_history_entry(self):
        _make_user()
        self.assertEqual(PasswordHistoryEntry.objects.count(), 0)

    def test_password_change_records_old_hash_in_history(self):
        user = _make_user()
        old_hash = user.password

        user.set_password("An0ther$ecurePass2")
        user.save()

        self.assertEqual(PasswordHistoryEntry.objects.filter(user=user).count(), 1)
        entry = PasswordHistoryEntry.objects.get(user=user)
        self.assertEqual(entry.password_hash, old_hash)

    def test_history_trimmed_to_depth_10(self):
        user = _make_user()
        for i in range(12):
            user.set_password(f"Passw0rd!Number{i:02d}XY")
            user.save()

        self.assertEqual(PasswordHistoryEntry.objects.filter(user=user).count(), 10)

    def test_resaving_without_password_change_does_not_grow_history(self):
        user = _make_user()
        user.set_password("An0ther$ecurePass2")
        user.save()
        self.assertEqual(PasswordHistoryEntry.objects.filter(user=user).count(), 1)

        user.position = "Другая должность"
        user.save()
        self.assertEqual(PasswordHistoryEntry.objects.filter(user=user).count(), 1)

    def test_password_changed_at_set_on_creation(self):
        user = _make_user()
        self.assertIsNotNone(user.password_changed_at)

    def test_password_changed_at_updates_on_change(self):
        user = _make_user()
        first_changed_at = user.password_changed_at

        user.set_password("An0ther$ecurePass2")
        user.save()
        user.refresh_from_db()

        self.assertGreater(user.password_changed_at, first_changed_at)


class PasswordHistoryValidatorTests(TestCase):
    def test_reused_password_rejected(self):
        user = _make_user()
        original_password = "Sup3r$ecret!Pass1"
        user.set_password("An0ther$ecurePass2")
        user.save()

        with self.assertRaises(ValidationError) as ctx:
            validate_password(original_password, user)
        self.assertIn("password_reused", [e.code for e in ctx.exception.error_list])

    def test_new_password_not_in_history_is_accepted(self):
        user = _make_user()
        user.set_password("An0ther$ecurePass2")
        user.save()

        # Не должно поднимать ValidationError по PasswordHistoryValidator
        # (могут сработать другие валидаторы — здесь пароль подобран так,
        # чтобы пройти все).
        validate_password("YetAnoth3r$SafePass3", user)

    def test_new_unsaved_user_skips_history_check(self):
        user = User(personnel_number="9999")
        validate_password("Whatever$Pass1234", user)

    def test_none_user_skips_history_check(self):
        validate_password("Whatever$Pass1234", None)


class PasswordExpiryTests(TestCase):
    """365-дневный срок действия пароля — ТОЛЬКО информационный флаг
    (решение Заказчика, честная граница как у password_change_required)."""

    def test_freshly_changed_password_not_expired(self):
        user = _make_user()
        self.assertFalse(user.is_password_expired)

    def test_password_changed_366_days_ago_is_expired(self):
        user = _make_user()
        User.objects.filter(pk=user.pk).update(
            password_changed_at=timezone.now() - timezone.timedelta(days=366)
        )
        user.refresh_from_db()
        self.assertTrue(user.is_password_expired)

    def test_null_password_changed_at_is_not_expired(self):
        user = _make_user()
        User.objects.filter(pk=user.pk).update(password_changed_at=None)
        user.refresh_from_db()
        self.assertFalse(user.is_password_expired)
