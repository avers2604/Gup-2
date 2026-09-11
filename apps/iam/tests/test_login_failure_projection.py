import inspect

from django.test import RequestFactory, TestCase

from apps.audit.models import AuditLog

from .. import services
from ..models import LoginFailure


class LoginFailureProjectionTests(TestCase):
    def test_iam_services_no_longer_imports_audit_model(self):
        source = inspect.getsource(services)
        self.assertNotIn("from apps.audit", source)
        self.assertNotIn("AuditLog.objects", source)

    def test_failed_credentials_write_operational_projection_and_worm_audit(self):
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

        audit = AuditLog.objects.get(event_type=AuditLog.EventType.SESSION_LOGIN_FAILED)
        self.assertEqual(audit.actor_personnel_number, "does-not-exist")
        self.assertEqual(audit.details["ip_address"], "203.0.113.17")
        self.assertEqual(audit.details["stage"], "credentials")

    def test_lockout_reads_projection_not_audit_log(self):
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            LoginFailure.objects.create(
                personnel_number="9001",
                ip_address="198.51.100.5",
                stage="credentials",
                reason="wrong_password",
            )
        self.assertTrue(services.is_locked_out("9001"))
        self.assertFalse(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.SESSION_LOGIN_FAILED,
                actor_personnel_number="9001",
            ).exists()
        )

    def test_ip_lockout_uses_indexed_column_not_json_details(self):
        for index in range(services.IP_LOCKOUT_MAX_ATTEMPTS):
            LoginFailure.objects.create(
                personnel_number=f"user-{index}",
                ip_address="192.0.2.77",
                stage="credentials",
                reason="wrong_password",
            )
        self.assertTrue(services.is_ip_locked_out("192.0.2.77"))

    def test_projection_has_personnel_and_ip_window_indexes(self):
        index_field_sets = {tuple(index.fields) for index in LoginFailure._meta.indexes}
        self.assertIn(("personnel_number", "created_at"), index_field_sets)
        self.assertIn(("ip_address", "created_at"), index_field_sets)
