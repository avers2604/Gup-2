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

    def test_admin_model_save_invalidates_stale_web_edit(self):
        fresh = NormativeDocument.objects.get(pk=self.document.pk)
        fresh.title = "Administrative correction"
        fresh.save()
        with self.assertRaises(ValidationError):
            services.update_document(actor=self.actor, document=self.document, title="Stale web edit")

    def test_ocr_only_save_does_not_invalidate_editorial_revision(self):
        fresh = NormativeDocument.objects.get(pk=self.document.pk)
        fresh.ocr_body = "Fresh OCR"
        fresh.save(update_fields=["ocr_body"])
        services.update_document(actor=self.actor, document=self.document, title="Editorial update")
        self.document.refresh_from_db()
        self.assertEqual(self.document.ocr_body, "Fresh OCR")
