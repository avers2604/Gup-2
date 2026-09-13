from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase

from apps.audit.admin import AuditLogAdmin
from apps.audit.models import AuditLog
from apps.documents.tests.test_permissions import make_user
from apps.iam.models import User


def _request(user):
    request = RequestFactory().get("/admin/audit/auditlog/")
    request.user = user
    return request


class AuditLogAdminPermissionTests(TestCase):
    def setUp(self):
        self.admin = AuditLogAdmin(AuditLog, AdminSite())

    def _grant_view(self, user):
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="audit",
                codename="view_auditlog",
            )
        )
        user.refresh_from_db()

    def test_plain_staff_with_django_permission_cannot_view_audit_log(self):
        reader = make_user(
            personnel_number="AUD-READER",
            role=User.Role.READER,
            is_staff=True,
        )
        self._grant_view(reader)

        self.assertFalse(self.admin.has_view_permission(_request(reader)))

    def test_security_officer_can_view_when_django_permission_is_present(self):
        officer = make_user(
            personnel_number="AUD-SEC",
            role=User.Role.SECURITY_OFFICER,
            is_staff=True,
        )
        self._grant_view(officer)

        self.assertTrue(self.admin.has_view_permission(_request(officer)))

    def test_audit_rows_cannot_be_created_from_admin(self):
        superuser = make_user(
            personnel_number="AUD-SUPER",
            role=User.Role.ADMINISTRATOR,
            is_staff=True,
            is_superuser=True,
        )

        self.assertFalse(self.admin.has_add_permission(_request(superuser)))
