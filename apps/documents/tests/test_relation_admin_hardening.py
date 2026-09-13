from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase

from apps.documents.admin import DocumentRelationAdmin
from apps.documents.models import DocumentRelation, NormativeDocument
from apps.iam.models import User

from .factories import make_document
from .test_permissions import make_user


def _grant(user, *codenames):
    user.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="documents",
            codename__in=codenames,
        )
    )
    user.refresh_from_db()


def _request(user):
    request = RequestFactory().get("/admin/documents/documentrelation/")
    request.user = user
    return request


class DocumentRelationAdminHardeningTests(TestCase):
    def setUp(self):
        self.admin = DocumentRelationAdmin(DocumentRelation, AdminSite())
        self.general = make_document(reg_number="REL-GENERAL")
        self.restricted = make_document(
            reg_number="REL-DSP",
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        self.relation = DocumentRelation.objects.create(
            from_document=self.general,
            to_document=self.restricted,
            relation_type=DocumentRelation.RelationType.REFERENCES,
        )

    def test_standalone_admin_is_readonly_even_with_django_model_permissions(self):
        controller = make_user(
            personnel_number="REL-ADMIN-WRITE",
            role=User.Role.CONTROLLER_LAWYER,
            is_staff=True,
            dsp_access=True,
        )
        _grant(
            controller,
            "add_documentrelation",
            "change_documentrelation",
            "delete_documentrelation",
        )
        request = _request(controller)

        self.assertFalse(self.admin.has_add_permission(request))
        self.assertFalse(self.admin.has_change_permission(request, self.relation))
        self.assertFalse(self.admin.has_delete_permission(request, self.relation))

    def test_relation_involving_dsp_document_is_hidden_without_dsp_clearance(self):
        reader = make_user(
            personnel_number="REL-ADMIN-READ",
            role=User.Role.READER,
            is_staff=True,
            dsp_access=False,
        )
        _grant(reader, "view_documentrelation")

        queryset = self.admin.get_queryset(_request(reader))

        self.assertFalse(queryset.filter(pk=self.relation.pk).exists())
