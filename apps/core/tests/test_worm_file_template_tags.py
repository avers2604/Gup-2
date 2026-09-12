from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from apps.core.templatetags.worm_files import worm_file_pending, worm_file_ready


class WormFileTemplateTagTests(SimpleTestCase):
    def test_pending_and_ready_share_one_promotion_lookup(self):
        meta = SimpleNamespace(label_lower="documents.normativedocument")
        instance = SimpleNamespace(
            pk="doc-1",
            _meta=meta,
            files_original=SimpleNamespace(name="documents/originals/doc.pdf"),
        )

        with mock.patch(
            "apps.core.templatetags.worm_files.promotion_pending_for",
            return_value=True,
        ) as promotion_pending:
            self.assertTrue(worm_file_pending(instance, "files_original"))
            self.assertFalse(worm_file_ready(instance, "files_original"))

        promotion_pending.assert_called_once_with(
            "documents.normativedocument",
            "doc-1",
            "files_original",
            "documents/originals/doc.pdf",
        )
