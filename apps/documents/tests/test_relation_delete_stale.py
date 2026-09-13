from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from apps.documents.models import DocumentRelation
from apps.iam.models import User

from .factories import make_document
from .test_permissions import PASSWORD, make_user


class RelationDeleteStaleViewTests(TestCase):
    def setUp(self):
        self.methodist = make_user(
            personnel_number="0212", role=User.Role.METHODIST
        )
        self.source = make_document(reg_number="R24-п")
        self.target = make_document(reg_number="R25-п")
        self.relation = DocumentRelation.objects.create(
            from_document=self.source,
            to_document=self.target,
            relation_type=DocumentRelation.RelationType.REFERENCES,
        )
        self.client_ = Client()
        self.client_.login(
            personnel_number=self.methodist.personnel_number,
            password=PASSWORD,
        )

    def test_already_removed_relation_does_not_report_success(self):
        url = reverse(
            "documents:relation_delete",
            args=[self.source.pk, self.relation.pk],
        )

        with patch(
            "apps.documents.relation_views.services.remove_relation",
            return_value=False,
        ):
            response = self.client_.post(url, follow=True)

        self.assertContains(response, "Связь уже была снята")
        self.assertNotContains(response, "Связь версионности снята.")
