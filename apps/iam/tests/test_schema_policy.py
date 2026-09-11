"""Регрессии account-policy для OpenAPI/Swagger endpoints."""

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.iam.models import User
from apps.iam.tests.test_auth_web import _make_user


class SchemaAccountPolicyTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.user.role = User.Role.ADMINISTRATOR
        self.user.save()

        token_client = APIClient()
        response = token_client.post(
            reverse("iam_api:token-obtain"),
            {
                "personnel_number": self.user.personnel_number,
                "password": "Sup3r$ecret!Pass",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["totp_required"])
        self.access = response.data["access"]

    def test_admin_without_required_totp_cannot_read_openapi_schema(self):
        """Схема API обязана соблюдать тот же AccountReady, что и сам API."""
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + self.access)

        response = client.get(reverse("schema"))

        self.assertEqual(response.status_code, 403)
