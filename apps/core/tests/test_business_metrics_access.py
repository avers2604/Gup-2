from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(BUSINESS_METRICS_TOKEN="metrics-test-token-that-is-long-enough")
class BusinessMetricsAccessTests(TestCase):
    def test_metrics_reject_missing_bearer_token(self):
        response = self.client.get(reverse("core:business-metrics"))
        self.assertEqual(response.status_code, 404)

    def test_metrics_reject_wrong_bearer_token(self):
        response = self.client.get(
            reverse("core:business-metrics"),
            HTTP_AUTHORIZATION="Bearer wrong-token",
        )
        self.assertEqual(response.status_code, 404)

    def test_metrics_accept_configured_bearer_token(self):
        response = self.client.get(
            reverse("core:business-metrics"),
            HTTP_AUTHORIZATION="Bearer metrics-test-token-that-is-long-enough",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response["Content-Type"])
