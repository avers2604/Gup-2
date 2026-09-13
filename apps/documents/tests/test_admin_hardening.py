from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase

from apps.documents.admin import (
    DocumentRelationInline,
    DocumentStatusHistoryInline,
    NormativeDocumentAdmin,
)
from apps.documents.models import DocumentRelation, DocumentStatusHistory, NormativeDocument
from apps.iam.models import User

from .factories import make_document
from .test_permissions import make_user


def _grant(user, app_label, *codenames):
    user.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label=app_label,
            codename__in=codenames,
        )
    )
    user.refresh_from_db()


def _request(user):
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


class NormativeDocumentAdminHardeningTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = NormativeDocumentAdmin(NormativeDocument, self.site)
        self.draft = make_document(reg_number="ADMIN-DRAFT")
        self.active = make_document(
            reg_number="ADMIN-ACTIVE",
            status=NormativeDocument.Status.ACTIVE,
        )

    def test_django_change_permission_does_not_override_domain_role(self):
        reader = make_user(personnel_number="ADM-READER", role=User.Role.READER, is_staff=True)
        _grant(reader, "documents", "change_normativedocument")

        self.assertFalse(self.admin.has_change_permission(_request(reader), self.draft))

    def test_editor_cannot_edit_document_in_force_through_admin(self):
        methodist = make_user(
            personnel_number="ADM-METHODIST",
            role=User.Role.METHODIST,
            is_staff=True,
        )
        _grant(methodist, "documents", "change_normativedocument")

        self.assertFalse(self.admin.has_change_permission(_request(methodist), self.active))
        self.assertTrue(self.admin.has_change_permission(_request(methodist), self.draft))

    def test_status_is_readonly_in_admin(self):
        controller = make_user(
            personnel_number="ADM-CONTROLLER",
            role=User.Role.CONTROLLER_LAWYER,
            is_staff=True,
        )
        _grant(controller, "documents", "change_normativedocument")

        readonly = self.admin.get_readonly_fields(_request(controller), self.draft)
        self.assertIn("status", readonly)

    def test_document_delete_is_disabled_even_with_django_permission(self):
        administrator = make_user(
            personnel_number="ADM-DELETE",
            role=User.Role.ADMINISTRATOR,
            is_staff=True,
        )
        _grant(administrator, "documents", "delete_normativedocument")

        self.assertFalse(self.admin.has_delete_permission(_request(administrator), self.draft))

    def test_relation_inline_is_readonly(self):
        controller = make_user(
            personnel_number="ADM-REL",
            role=User.Role.CONTROLLER_LAWYER,
            is_staff=True,
        )
        _grant(
            controller,
            "documents",
            "add_documentrelation",
            "change_documentrelation",
            "delete_documentrelation",
        )
        inline = DocumentRelationInline(NormativeDocument, self.site)
        request = _request(controller)

        self.assertFalse(inline.has_add_permission(request, self.draft))
        self.assertFalse(inline.has_change_permission(request, None))
        self.assertFalse(inline.has_delete_permission(request, None))

    def test_status_history_inline_is_readonly(self):
        controller = make_user(
            personnel_number="ADM-HIST",
            role=User.Role.CONTROLLER_LAWYER,
            is_staff=True,
        )
        _grant(
            controller,
            "documents",
            "add_documentstatushistory",
            "change_documentstatushistory",
            "delete_documentstatushistory",
        )
        inline = DocumentStatusHistoryInline(NormativeDocument, self.site)
        request = _request(controller)

        self.assertFalse(inline.has_add_permission(request, self.draft))
        self.assertFalse(inline.has_change_permission(request, None))
        self.assertFalse(inline.has_delete_permission(request, None))
