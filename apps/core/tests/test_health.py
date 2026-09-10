from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse


class HealthEndpointTests(TestCase):
    def test_health_returns_healthy_when_database_is_reachable(self):
        response = self.client.get(reverse("core:health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"healthy\n")
        self.assertEqual(response["Cache-Control"], "max-age=0, no-cache, no-store, must-revalidate, private")

    @patch("apps.core.views.connection.cursor", side_effect=DatabaseError("db unavailable"))
    def test_health_returns_503_when_database_is_unreachable(self, _cursor):
        response = self.client.get(reverse("core:health"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"unhealthy\n")

    def test_health_rejects_post(self):
        response = self.client.post(reverse("core:health"))

        self.assertEqual(response.status_code, 405)
