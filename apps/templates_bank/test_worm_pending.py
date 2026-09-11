from django.test import Client, TestCase
from django.urls import reverse

from apps.core.models import StagedFilePromotion
from apps.documents.tests.factories import make_document
from apps.documents.tests.test_permissions import make_user

from .models import Template, TemplateFamily


class TemplateWormPendingTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0399")
        self.family = TemplateFamily.objects.create(name="WORM pending form")
        self.template = Template.objects.create(
            family=self.family,
            version="v1.0",
            change_type=Template.ChangeType.MAJOR,
            status=Template.Status.ACTIVE,
            approving_document=make_document(reg_number="WORM-TPL-APPROVE"),
            file_editable="templates/editable/2026/form.docx",
            file_sample="templates/samples/2026/sample.pdf",
        )
        self.promotion = StagedFilePromotion.objects.create(
            model_label="templates_bank.template",
            object_id=str(self.template.pk),
            field_name="file_editable",
            staging_name="staging/worm/template/form.docx",
            destination_name=self.template.file_editable.name,
            legal_hold=True,
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_pending_download_returns_409_and_is_not_counted(self):
        response = self.client.get(
            reverse("templates_bank:download", args=[self.template.pk, "file_editable"])
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response["Retry-After"], "5")
        self.template.refresh_from_db()
        self.assertEqual(self.template.download_count, 0)

    def test_family_ui_marks_file_as_securing_and_hides_download_link(self):
        download_url = reverse(
            "templates_bank:download", args=[self.template.pk, "file_editable"]
        )
        response = self.client.get(
            reverse("templates_bank:family_detail", args=[self.family.pk])
        )
        self.assertContains(response, "закрепляется в защищённом хранилище")
        self.assertNotContains(response, f'href="{download_url}"')
