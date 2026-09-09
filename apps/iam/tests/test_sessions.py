from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.test import TestCase

from ..models import Department, User
from ..sessions import force_logout_user


def _make_user(personnel_number="0001", **kwargs):
    dept, _ = Department.objects.get_or_create(
        name="Служба движения", defaults={"level": Department.Level.SERVICE}
    )
    defaults = dict(
        personnel_number=personnel_number, last_name="Иванов", first_name="Пётр",
        position="Водитель", department=dept, role=User.Role.READER,
    )
    defaults.update(kwargs)
    return User.objects.create(**defaults)


def _make_session_for(user: User) -> str:
    store = SessionStore()
    store["_auth_user_id"] = str(user.pk)
    store.save()
    return store.session_key


class ForceLogoutUserTests(TestCase):
    """apps/iam/sessions.py — ТЗ 4.7: принудительный сброс сессий при блокировке."""

    def test_deletes_only_sessions_of_given_user(self):
        user_a = _make_user("0001")
        user_b = _make_user("0002")
        key_a = _make_session_for(user_a)
        key_b = _make_session_for(user_b)

        deleted = force_logout_user(user_a.pk)

        self.assertEqual(deleted, 1)
        self.assertFalse(Session.objects.filter(session_key=key_a).exists())
        self.assertTrue(Session.objects.filter(session_key=key_b).exists())

    def test_multiple_sessions_of_same_user_all_deleted(self):
        # Проект пока не запрещает параллельные сессии (тоже ТЗ 4.7,
        # отдельная нереализованная часть) — блокировка должна убивать
        # ВСЕ сессии пользователя, а не только одну.
        user = _make_user("0001")
        key_1 = _make_session_for(user)
        key_2 = _make_session_for(user)

        deleted = force_logout_user(user.pk)

        self.assertEqual(deleted, 2)
        self.assertFalse(Session.objects.filter(session_key__in=[key_1, key_2]).exists())

    def test_no_sessions_returns_zero(self):
        user = _make_user("0001")
        self.assertEqual(force_logout_user(user.pk), 0)


class UserBlockingInvalidatesSessionsTests(TestCase):
    """Интеграция: сохранение status=blocked через User.save() реально
    вызывает сброс сессий, а не только меняет поле в БД (задание)."""

    def test_transition_to_blocked_deletes_session(self):
        user = _make_user("0001")
        session_key = _make_session_for(user)
        self.assertTrue(Session.objects.filter(session_key=session_key).exists())

        user.status = User.Status.BLOCKED
        user.save()

        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        self.assertFalse(user.is_active)

    def test_resaving_already_blocked_user_does_not_error(self):
        user = _make_user("0001", status=User.Status.BLOCKED)
        session_key = _make_session_for(user)  # сессия создана уже ПОСЛЕ блокировки — гипотетический случай для теста повторного save()

        user.position = "Обновлённая должность"
        user.save()  # не должно упасть и не обязано трогать эту сессию — не переход, а «уже заблокирован»

        self.assertTrue(Session.objects.filter(session_key=session_key).exists())

    def test_creating_user_already_blocked_does_not_call_force_logout_incorrectly(self):
        # Новый пользователь, сразу созданный заблокированным (например,
        # импортом) — не «переход», сбрасывать нечего, но и падать не должно.
        user = _make_user("0002", status=User.Status.BLOCKED)
        self.assertFalse(user.is_active)

    def test_transition_away_from_blocked_does_not_touch_sessions(self):
        user = _make_user("0001", status=User.Status.BLOCKED)
        user.status = User.Status.ACTIVE
        user.save()
        self.assertTrue(user.is_active)
