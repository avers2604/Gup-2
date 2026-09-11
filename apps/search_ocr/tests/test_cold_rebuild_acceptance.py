import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase

from apps.documents.tests.factories import make_document
from apps.search_ocr.search_index import DocumentSearchIndex


class ColdRebuildAcceptanceTests(TransactionTestCase):
    reset_sequences = True

    def _cold(self, **overrides):
        options = {
            "cold": True,
            "require_count": 3,
            "confirm_cold_rebuild": "TRUNCATE_DOCUMENT_SEARCH_INDEX",
            "max_seconds": 3600,
        }
        options.update(overrides)
        with patch.dict(
            "os.environ",
            {"ACCEPTANCE_ALLOW_DESTRUCTIVE_REINDEX": "YES"},
            clear=False,
        ):
            return call_command("rebuild_search_index", **options)

    def test_cold_command_requires_exact_corpus_guard(self):
        with self.assertRaisesRegex(CommandError, "--cold requires --require-count"):
            call_command("rebuild_search_index", cold=True)

    def test_cold_command_requires_explicit_acceptance_environment_opt_in(self):
        for index in range(3):
            make_document(reg_number=f"GUARD-ENV-{index}")
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(
                CommandError, "ACCEPTANCE_ALLOW_DESTRUCTIVE_REINDEX=YES"
            ):
                call_command(
                    "rebuild_search_index",
                    cold=True,
                    require_count=3,
                    confirm_cold_rebuild="TRUNCATE_DOCUMENT_SEARCH_INDEX",
                )
        self.assertEqual(DocumentSearchIndex.objects.count(), 3)

    def test_cold_command_requires_explicit_confirmation_phrase(self):
        for index in range(3):
            make_document(reg_number=f"GUARD-PHRASE-{index}")
        with patch.dict(
            "os.environ",
            {"ACCEPTANCE_ALLOW_DESTRUCTIVE_REINDEX": "YES"},
            clear=False,
        ):
            with self.assertRaisesRegex(CommandError, "--confirm-cold-rebuild"):
                call_command(
                    "rebuild_search_index",
                    cold=True,
                    require_count=3,
                )
        self.assertEqual(DocumentSearchIndex.objects.count(), 3)

    def test_cold_command_truncates_existing_read_model_and_reports_cold_mode(self):
        for index in range(3):
            make_document(reg_number=f"COLD-{index}", title=f"Документ {index}")
        self.assertEqual(DocumentSearchIndex.objects.count(), 3)

        # Prove the run does not merely UPSERT the existing read model: add a
        # sentinel row state that can survive only if the table is not emptied.
        DocumentSearchIndex.objects.filter(pk=DocumentSearchIndex.objects.first().pk).update(
            combined_vector="'sentinel':1"
        )

        output = io.StringIO()
        self._cold(batch_size=2, as_json=True, stdout=output)

        self.assertEqual(DocumentSearchIndex.objects.count(), 3)
        rendered = output.getvalue()
        self.assertIn('"mode": "cold"', rendered)
        self.assertIn('"documents": 3', rendered)
        self.assertNotIn("sentinel", str(DocumentSearchIndex.objects.first().combined_vector))

    def test_corpus_guard_uses_source_documents_not_current_index_size(self):
        for index in range(3):
            make_document(reg_number=f"SOURCE-{index}")
        # Deliberately make the persisted search index incomplete. Acceptance
        # corpus size must still be derived from NormativeDocument source rows.
        DocumentSearchIndex.objects.exclude(pk=DocumentSearchIndex.objects.first().pk).delete()
        self.assertEqual(DocumentSearchIndex.objects.count(), 1)

        self._cold(require_count=3)

        self.assertEqual(DocumentSearchIndex.objects.count(), 3)

    def test_cold_command_refuses_wrong_source_corpus_size_before_truncate(self):
        make_document(reg_number="COLD-ONLY")
        before = DocumentSearchIndex.objects.count()

        with patch.dict(
            "os.environ",
            {"ACCEPTANCE_ALLOW_DESTRUCTIVE_REINDEX": "YES"},
            clear=False,
        ):
            with self.assertRaises(CommandError):
                call_command(
                    "rebuild_search_index",
                    cold=True,
                    require_count=10000,
                    confirm_cold_rebuild="TRUNCATE_DOCUMENT_SEARCH_INDEX",
                )

        self.assertEqual(DocumentSearchIndex.objects.count(), before)
