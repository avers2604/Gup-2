from django.conf import settings
from django.test import SimpleTestCase

from config.celery import app


class CelerySettingsImmutabilityTests(SimpleTestCase):
    def test_visibility_timeout_is_runtime_config_only(self):
        self.assertIn("visibility_timeout", app.conf.broker_transport_options)
        self.assertNotIn(
            "visibility_timeout",
            settings.CELERY_BROKER_TRANSPORT_OPTIONS,
        )
