from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase

from apps.documents.tests.test_permissions import make_user
from apps.iam.models import User
from apps.templates_bank.admin import TemplateFamilyAdmin
from apps.templates_bank.models import TemplateFamily


def _grant(user, *codenames):
    user.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="templates_bank",
            codename__in=codenames,
        )
    )
    user.refresh_from_db()


def _request(user):
    request = RequestFactory().get("/admin/templates_bank/templatefamily/")
    request.user = user
    return request


class TemplateFamilyAdminHardeningTests(TestCase):
    def setUp(self):
        self.admin = TemplateFamilyAdmin(TemplateFamily, AdminSite())

    def test_plain_staff_cannot_manage_families_via_django_permission(self):
        reader = make_user(
            personnel_number="TPL-READER",
            role=User.Role.READER,
            is_staff=True,
        )
        _grant(
            reader,
            "add_templatefamily",
            "change_templatefamily",
            "delete_templatefamily",
        )
        request = _request(reader)

        self.assertFalse(self.admin.has_add_permission(request))
        self.assertFalse(self.admin.has_change_permission(request))
        self.assertFalse(self.admin.has_delete_permission(request))

    def test_template_manager_can_add_and_change_family(self):
        controller = make_user(
            personnel_number="TPL-CONTROLLER",
            role=User.Role.CONTROLLER_LAWYER,
            is_staff=True,
        )
        _grant(controller, "add_templatefamily", "change_templatefamily")
        request = _request(controller)

        self.assertTrue(self.admin.has_add_permission(request))
        self.assertTrue(self.admin.has_change_permission(request))

    def test_family_delete_is_disabled(self):
        administrator = make_user(
            personnel_number="TPL-DELETE",
            role=User.Role.ADMINISTRATOR,
            is_staff=True,
        )
        _grant(administrator, "delete_templatefamily")

        self.assertFalse(self.admin.has_delete_permission(_request(administrator)))
