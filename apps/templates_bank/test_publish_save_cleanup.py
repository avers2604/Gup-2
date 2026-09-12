from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase

from apps.documents.models import NormativeDocument
from apps.documents.tests.factories import make_document
from apps.documents.tests.test_permissions import make_user
from apps.iam.models import User

from . import services
from .models import Template, TemplateFamily


class TemplatePublishSaveCleanupTests(TestCase):
    """Staging created by Template.save() must be discarded if DB save fails."""

    def setUp(self):
        self.controller = make_user(
            personnel_number="0542", role=User.Role.CONTROLLER_LAWYER
        )
        self.family = TemplateFamily.objects.create(name="Бланк аварийного акта")
        self.approving = make_document(
            reg_number="TPL-SAVE-FAIL",
            status=NormativeDocument.Status.ACTIVE,
        )

    def test_failed_template_save_discards_staged_uploads(self):
        attrs = {
            "version": "v2.0",
            "change_type": Template.ChangeType.MAJOR,
            "approving_document": self.approving,
            "file_editable": "templates/editable/2026/fail.docx",
            "file_sample": "templates/samples/2026/fail.pdf",
        }

        with patch.object(
            Template,
            "save",
            side_effect=IntegrityError("unique_template_version"),
        ), patch(
            "apps.core.staged_files.discard_staged_uploads"
        ) as discard:
            with self.assertRaises(IntegrityError):
                services.publish_version(
                    actor=self.controller,
                    family=self.family,
                    **attrs,
                )

        discard.assert_called_once()
        failed_template = discard.call_args.args[0]
        self.assertEqual(failed_template.family, self.family)
        self.assertEqual(failed_template.version, "v2.0")
