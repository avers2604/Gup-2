"""config/celery.py — visibility_timeout must actually reach the broker.

Regression for Ф-8 (docs/STAGE4_LAB_REHEARSAL.md): `app.conf.X = {...}` is a
silent no-op for settings already populated via config_from_object(), because
celery.app.utils.Settings resolves such names against the object passed to
config_from_object() before it looks at later attribute assignments. The
previous code reassigned `app.conf.broker_transport_options` outright, which
never took effect — the real runtime value silently fell back to kombu's
built-in default (3600s) instead of the intended, safety-checked value,
making the queue-drill's documented 15-minute Celery/Redis kill -9 recovery
window untrue in practice. `.update()` mutates the existing dict in place and
does take effect; this test asserts against the live broker connection, not
just against `app.conf`, so a future regression back to plain reassignment
fails here even though `app.conf.broker_transport_options` would still look
correct until read through a real connection.
"""
from django.test import TestCase

from config.celery import app


class CeleryVisibilityTimeoutTests(TestCase):
    def test_broker_transport_options_contains_visibility_timeout(self):
        self.assertIn("visibility_timeout", app.conf.broker_transport_options)

    def test_visibility_timeout_reaches_the_live_broker_connection(self):
        # The regression this guards against only shows up on an actual
        # connection's QoS object, not on app.conf itself — reading app.conf
        # alone would have passed even with the bug.
        with app.connection_or_acquire() as conn:
            channel = conn.default_channel
            self.assertEqual(
                channel.qos.visibility_timeout,
                app.conf.broker_transport_options["visibility_timeout"],
            )
            # Also pin the actual configured value, not just self-consistency
            # with app.conf (which could itself be wrong).
            self.assertGreater(channel.qos.visibility_timeout, 600)
