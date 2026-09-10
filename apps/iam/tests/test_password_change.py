"""password_change_required — по запросу ревью анти-фрода превращён из
информационного флага в реальное ограничение (apps/iam/middleware.py,
apps/iam/views.PasswordChangeView, apps/iam/forms.PasswordChangeForm)."""
import datetime

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from ..models import PASSWORD_EXPIRY_DAYS, Department, User

OLD_PASSWORD = "Sup3r$ecret!Pass"
NEW_PASSWORD = "AnotherStr0ng!Pass"


def _make_user(**kwargs):
    dept, _ = Department.objects.get_or_create(
        name="Служба движения", defaults={"level": Department.Level.SERVICE}
    )
    defaults = dict(
        personnel_number="0001", last_name="Иванов", first_name="Пётр",
        position="Водитель", department=dept, role=User.Role.READER,
        status=User.Status.ACTIVE,
    )
    defaults.update(kwargs)
    user = User(**defaults)
    user.set_password(OLD_PASSWORD)
    user.save()
    return user


class PasswordChangeRequiredMiddlewareTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_status_password_change_required_redirects_to_change_page(self):
        _make_user(status=User.Status.PASSWORD_CHANGE_REQUIRED)
        self.client.login(personnel_number="0001", password=OLD_PASSWORD)
        response = self.client.get(reverse("search_ocr:search"))
        self.assertRedirects(response, reverse("iam:password-change"))

    def test_active_user_not_redirected(self):
        _make_user()
        self.client.login(personnel_number="0001", password=OLD_PASSWORD)
        response = self.client.get(reverse("search_ocr:search"))
        self.assertEqual(response.status_code, 200)

    def test_expired_password_redirects_to_change_page(self):
        user = _make_user()
        old_moment = timezone.now() - datetime.timedelta(days=PASSWORD_EXPIRY_DAYS + 1)
        User.objects.filter(pk=user.pk).update(password_changed_at=old_moment)
        self.client.login(personnel_number="0001", password=OLD_PASSWORD)
        response = self.client.get(reverse("search_ocr:search"))
        self.assertRedirects(response, reverse("iam:password-change"))

    def test_password_change_page_itself_is_not_redirected(self):
        _make_user(status=User.Status.PASSWORD_CHANGE_REQUIRED)
        self.client.login(personnel_number="0001", password=OLD_PASSWORD)
        response = self.client.get(reverse("iam:password-change"))
        self.assertEqual(response.status_code, 200)

    def test_logout_is_not_blocked_while_change_required(self):
        _make_user(status=User.Status.PASSWORD_CHANGE_REQUIRED)
        self.client.login(personnel_number="0001", password=OLD_PASSWORD)
        response = self.client.post(reverse("iam:logout"))
        self.assertEqual(response.status_code, 302)

    def test_anonymous_request_is_not_redirected(self):
        response = self.client.get(reverse("search_ocr:search"))
        # Обычный редирект LoginRequiredMixin на страницу входа — не на
        # смену пароля (неаутентифицированный запрос вообще не должен
        # попадать под эту логику).
        self.assertNotEqual(response.headers.get("Location"), reverse("iam:password-change"))


class PasswordChangeViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = _make_user(status=User.Status.PASSWORD_CHANGE_REQUIRED)
        self.client.login(personnel_number="0001", password=OLD_PASSWORD)

    def test_successful_change_clears_status_and_redirects(self):
        response = self.client.post(reverse("iam:password-change"), {
            "old_password": OLD_PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        })
        self.assertEqual(response.status_code, 302)

        self.user.refresh_from_db()
        self.assertEqual(self.user.status, User.Status.ACTIVE)
        self.assertTrue(self.user.check_password(NEW_PASSWORD))

    def test_session_survives_password_change(self):
        # update_session_auth_hash() — без него сессия была бы
        # инвалидирована следующим же запросом (Django привязывает её к
        # хэшу пароля), разлогинив только что сменившего пароль пользователя.
        self.client.post(reverse("iam:password-change"), {
            "old_password": OLD_PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        })
        response = self.client.get(reverse("search_ocr:search"))
        self.assertEqual(response.status_code, 200)

    def test_wrong_old_password_rejected(self):
        response = self.client.post(reverse("iam:password-change"), {
            "old_password": "totally-wrong",
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.status, User.Status.PASSWORD_CHANGE_REQUIRED)

    def test_weak_new_password_rejected_by_validators(self):
        response = self.client.post(reverse("iam:password-change"), {
            "old_password": OLD_PASSWORD,
            "new_password1": "short",
            "new_password2": "short",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.status, User.Status.PASSWORD_CHANGE_REQUIRED)

    def test_active_status_untouched_by_unrelated_password_change(self):
        # Не единственный сценарий смены пароля — обычный ACTIVE-пользователь
        # тоже может сменить пароль (не только принудительно). status не
        # должен от этого превратиться во что-то иное.
        User.objects.filter(pk=self.user.pk).update(status=User.Status.ACTIVE)
        self.client.post(reverse("iam:password-change"), {
            "old_password": OLD_PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        })
        self.user.refresh_from_db()
        self.assertEqual(self.user.status, User.Status.ACTIVE)
