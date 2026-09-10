from django.core.exceptions import ValidationError
from django.test import TestCase
from apps.documents import services
from apps.documents.models import NormativeDocument
from apps.documents.tests.factories import make_document
from apps.iam.models import User
from apps.iam.tests.test_auth_web import _make_user


class ConcurrentEditTests(TestCase):
    def setUp(self):
        self.actor = _make_user(role=User.Role.METHODIST)
        self.document = make_document(reg_number="EDIT-1", files_original="original.pdf")

    def test_ocr_result_is_preserved_when_old_instance_is_saved(self):
        NormativeDocument.objects.filter(pk=self.document.pk).update(ocr_body="Recognized meanwhile")
        services.update_document(actor=self.actor, document=self.document, title="Updated title")
        self.document.refresh_from_db()
        self.assertEqual(self.document.ocr_body, "Recognized meanwhile")
        self.assertEqual(self.document.title, "Updated title")

    def test_stale_edit_is_rejected(self):
        stale = NormativeDocument.objects.get(pk=self.document.pk)
        services.update_document(actor=self.actor, document=self.document, title="First edit")
        with self.assertRaises(ValidationError):
            services.update_document(actor=self.actor, document=stale, title="Stale edit")
        stale.refresh_from_db()
        self.assertEqual(stale.title, "First edit")
