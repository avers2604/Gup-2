from django.test import TestCase
from django.urls import reverse

from apps.core.models import StagedFilePromotion
from apps.documents.tests.test_permissions import make_user

from .factories import make_document


class DocumentWormPendingUiTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="98100")
        self.client.force_login(self.user)
        self.document = make_document(
            reg_number="WORM-UI",
            files_original="documents/originals/2026/09/pending.pdf",
        )
        StagedFilePromotion.objects.create(
            model_label="documents.normativedocument",
            object_id=str(self.document.pk),
            field_name="files_original",
            staging_name="staging/worm/doc/pending.pdf",
            destination_name=self.document.files_original.name,
        )

    def test_detail_marks_original_as_securing_and_hides_download_link(self):
        download_url = reverse(
            "documents:file-link", args=[self.document.pk, "original"]
        )
        response = self.client.get(reverse("documents:detail", args=[self.document.pk]))
        self.assertContains(response, "Закрепляется в защищённом хранилище")
        self.assertNotContains(response, f'href="{download_url}"')
