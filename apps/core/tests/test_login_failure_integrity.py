from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.audit.models import AuditLog
from apps.iam.models import LoginFailure


class LoginFailureIntegrityCommandTests(TestCase):
    def _projection(self, personnel="0001", ip="192.0.2.10", stage="credentials", reason="wrong_credentials"):
        return LoginFailure.objects.create(
            personnel_number=personnel,
            ip_address=ip,
            stage=stage,
            reason=reason,
        )

    def _audit(self, personnel="0001", ip="192.0.2.10", stage="credentials", reason="wrong_credentials"):
        return AuditLog.objects.create(
            event_type=AuditLog.EventType.SESSION_LOGIN_FAILED,
            actor_personnel_number=personnel,
            object_type="User",
            object_id="",
            details={"ip_address": ip, "stage": stage, "reason": reason},
        )

    def test_matching_projection_and_worm_passes_without_mutation(self):
        self._projection()
        self._audit()
        projection_before = LoginFailure.objects.count()
        audit_before = AuditLog.objects.count()
        out = StringIO()

        call_command(
            "check_login_failure_integrity",
            window_minutes=15,
            settle_seconds=0,
            stdout=out,
        )

        self.assertIn("PASS", out.getvalue())
        self.assertEqual(LoginFailure.objects.count(), projection_before)
        self.assertEqual(AuditLog.objects.count(), audit_before)

    def test_projection_without_worm_fails_with_actionable_diagnostics(self):
        self._projection(personnel="projection-only")
        out = StringIO()

        with self.assertRaisesMessage(
            CommandError,
            "projection_without_audit=1, audit_without_projection=0",
        ):
            call_command(
                "check_login_failure_integrity",
                window_minutes=15,
                settle_seconds=0,
                stdout=out,
            )

        self.assertIn("projection-only", out.getvalue())

    def test_worm_without_projection_fails_with_actionable_diagnostics(self):
        self._audit(personnel="audit-only")
        out = StringIO()

        with self.assertRaisesMessage(
            CommandError,
            "projection_without_audit=0, audit_without_projection=1",
        ):
            call_command(
                "check_login_failure_integrity",
                window_minutes=15,
                settle_seconds=0,
                stdout=out,
            )

        self.assertIn("audit-only", out.getvalue())

    def test_legacy_audit_normalization_matches_migration_semantics(self):
        self._projection(
            personnel="legacy-user",
            ip="198.51.100.7",
            stage="credentials",
            reason="legacy_audit",
        )
        AuditLog.objects.create(
            event_type=AuditLog.EventType.SESSION_LOGIN_FAILED,
            actor_personnel_number="legacy-user",
            object_type="User",
            object_id="",
            details={"ip_address": "198.51.100.7", "stage": "credentials"},
        )
        out = StringIO()

        call_command(
            "check_login_failure_integrity",
            window_minutes=15,
            settle_seconds=0,
            stdout=out,
        )

        self.assertIn("PASS", out.getvalue())

    def test_rejects_invalid_window_and_settle_values(self):
        with self.assertRaises(CommandError):
            call_command("check_login_failure_integrity", window_minutes=0)
        with self.assertRaises(CommandError):
            call_command("check_login_failure_integrity", settle_seconds=-1)
        with self.assertRaises(CommandError):
            call_command(
                "check_login_failure_integrity",
                window_minutes=1,
                settle_seconds=30,
            )
