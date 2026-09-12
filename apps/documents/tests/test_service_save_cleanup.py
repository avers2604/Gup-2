import datetime
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase

from apps.documents import services
from apps.documents.models import NormativeDocument
from apps.documents.retention import RetentionCategory
from apps.iam.models import Department, User

from .factories import make_document
from .test_permissions import make_user


class DocumentSaveFailureCleanupTests(TestCase):
    """Mutable uploads must be compensated even when model.save() itself fails."""

    def setUp(self):
        self.methodist = make_user(
            personnel_number="0540", role=User.Role.METHODIST
        )
        self.department, _ = Department.objects.get_or_create(
            name="Служба движения",
            defaults={"level": Department.Level.SERVICE},
        )

    def _create_attrs(self):
        return {
            "reg_number": "SAVE-FAIL-1",
            "reg_date": datetime.date(2026, 9, 12),
            "effective_date": datetime.date(2026, 9, 13),
            "doc_type": NormativeDocument.DocType.ORDER,
            "title": "Проверка компенсации файла",
            "issuer_dept": self.department,
            "retention_category": RetentionCategory.ORDERS_CORE,
            "files_editable": SimpleUploadedFile("failed.docx", b"docx bytes"),
        }

    def test_create_cleans_new_mutable_file_when_save_fails(self):
        with patch.object(
            NormativeDocument,
            "save",
            side_effect=IntegrityError("concurrent unique collision"),
        ), patch(
            "apps.documents.services._cleanup_failed_file_writes"
        ) as cleanup:
            with self.assertRaises(IntegrityError):
                services.create_document(
                    actor=self.methodist,
                    **self._create_attrs(),
                )

        cleanup.assert_called_once()
        self.assertEqual(cleanup.call_args.args[1], {"files_editable": ""})

    def test_update_cleans_replacement_mutable_file_when_save_fails(self):
        document = make_document(
            reg_number="SAVE-FAIL-2",
            files_editable="documents/editable/2026/old.docx",
        )
        replacement = SimpleUploadedFile("replacement.docx", b"new docx bytes")

        with patch.object(
            NormativeDocument,
            "save",
            side_effect=IntegrityError("write lost race"),
        ), patch(
            "apps.documents.services._cleanup_failed_file_writes"
        ) as cleanup:
            with self.assertRaises(IntegrityError):
                services.update_document(
                    actor=self.methodist,
                    document=document,
                    files_editable=replacement,
                )

        cleanup.assert_called_once()
        self.assertEqual(
            cleanup.call_args.args[1],
            {"files_editable": "documents/editable/2026/old.docx"},
        )
