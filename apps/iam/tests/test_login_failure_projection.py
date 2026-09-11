import inspect

from django.test import RequestFactory, TestCase

from apps.audit.models import AuditLog
from apps.core import domain_events

from .. import services
from ..models import LoginFailure


class LoginFailureProjectionTests(TestCase):
    def test_iam_services_no_longer_imports_audit_model(self):
        source = inspect.getsource(services)
        self.assertNotIn("from apps.audit", source)
        self.assertNotIn("AuditLog.objects", source)

    def test_failed_credentials_write_projection_and_worm_audit(self):
        request = RequestFactory().post("/", REMOTE_ADDR="203.0.113.17")
        result = services.check_credentials(
            request,
            personnel_number="does-not-exist",
            password="wrong",
        )
        self.assertIsNone(result)

        failure = LoginFailure.objects.get()
        self.assertEqual(failure.personnel_number, "does-not-exist")
        self.assertEqual(failure.ip_address, "203.0.113.17")
        self.assertEqual(failure.stage, "credentials")
        self.assertEqual(failure.reason, "wrong_credentials")

        audit = AuditLog.objects.get(event_type=AuditLog.EventType.SESSION_LOGIN_FAILED)
        self.assertEqual(audit.actor_personnel_number, "does-not-exist")
        self.assertEqual(audit.details["ip_address"], "203.0.113.17")
        self.assertEqual(audit.details["stage"], "credentials")
        self.assertEqual(audit.details["reason"], "wrong_credentials")

    def test_projection_rolls_back_if_critical_audit_handler_is_missing(self):
        handlers = domain_events._HANDLERS.pop("auth.login.failed")
        try:
            with self.assertRaises(domain_events.MissingDomainEventHandler):
                services.check_credentials(
                    RequestFactory().post("/", REMOTE_ADDR="203.0.113.19"),
                    personnel_number="missing-handler",
                    password="wrong",
                )
            self.assertFalse(
                LoginFailure.objects.filter(personnel_number="missing-handler").exists()
            )
        finally:
            domain_events._HANDLERS["auth.login.failed"] = handlers

    def test_lockout_reads_projection_not_worm_audit(self):
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            LoginFailure.objects.create(
                personnel_number="9001",
                ip_address="198.51.100.5",
                stage="credentials",
                reason="wrong_credentials",
            )

        self.assertTrue(services.is_locked_out("9001"))
        self.assertFalse(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.SESSION_LOGIN_FAILED,
                actor_personnel_number="9001",
            ).exists()
        )

    def test_ip_lockout_uses_indexed_projection_column(self):
        for index in range(services.IP_LOCKOUT_MAX_ATTEMPTS):
            LoginFailure.objects.create(
                personnel_number=f"user-{index}",
                ip_address="192.0.2.77",
                stage="credentials",
                reason="wrong_credentials",
            )

        self.assertTrue(services.is_ip_locked_out("192.0.2.77"))

    def test_projection_has_personnel_and_ip_window_indexes(self):
        index_field_sets = {tuple(index.fields) for index in LoginFailure._meta.indexes}
        self.assertIn(("personnel_number", "created_at"), index_field_sets)
        self.assertIn(("ip_address", "created_at"), index_field_sets)
