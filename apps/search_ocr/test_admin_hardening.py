from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase

from apps.documents.tests.test_permissions import make_user
from apps.iam.models import User
from apps.search_ocr.admin import ThesaurusEntryAdmin
from apps.search_ocr.models import ThesaurusEntry


def _grant(user, *codenames):
    user.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="search_ocr",
            codename__in=codenames,
        )
    )
    user.refresh_from_db()


def _request(user):
    request = RequestFactory().get("/admin/search_ocr/thesaurusentry/")
    request.user = user
    return request


class ThesaurusEntryAdminHardeningTests(TestCase):
    def setUp(self):
        self.admin = ThesaurusEntryAdmin(ThesaurusEntry, AdminSite())

    def test_django_permission_does_not_override_business_role(self):
        reader = make_user(
            personnel_number="TH-READER",
            role=User.Role.READER,
            is_staff=True,
        )
        _grant(reader, "change_thesaurusentry")

        self.assertFalse(self.admin.has_change_permission(_request(reader)))

    def test_controller_can_open_readonly_change_form(self):
        controller = make_user(
            personnel_number="TH-CONTROLLER",
            role=User.Role.CONTROLLER_LAWYER,
            is_staff=True,
        )
        _grant(controller, "change_thesaurusentry")

        self.assertTrue(self.admin.has_change_permission(_request(controller)))
        readonly = set(self.admin.get_readonly_fields(_request(controller)))
        self.assertTrue({field.name for field in ThesaurusEntry._meta.fields} <= readonly)

    def test_manual_add_and_delete_are_disabled(self):
        controller = make_user(
            personnel_number="TH-MUTATE",
            role=User.Role.CONTROLLER_LAWYER,
            is_staff=True,
        )
        _grant(controller, "add_thesaurusentry", "delete_thesaurusentry")
        request = _request(controller)

        self.assertFalse(self.admin.has_add_permission(request))
        self.assertFalse(self.admin.has_delete_permission(request))
