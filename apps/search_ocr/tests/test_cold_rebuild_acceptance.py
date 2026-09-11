import io

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase

from apps.documents.tests.factories import make_document
from apps.search_ocr.search_index import DocumentSearchIndex


class ColdRebuildAcceptanceTests(TransactionTestCase):
    reset_sequences = True

    def test_cold_command_requires_exact_corpus_guard(self):
        with self.assertRaisesRegex(CommandError, "--cold requires --require-count"):
            call_command("rebuild_search_index", cold=True)

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
        call_command(
            "rebuild_search_index",
            cold=True,
            batch_size=2,
            require_count=3,
            max_seconds=3600,
            as_json=True,
            stdout=output,
        )

        self.assertEqual(DocumentSearchIndex.objects.count(), 3)
        rendered = output.getvalue()
        self.assertIn('"mode": "cold"', rendered)
        self.assertIn('"documents": 3', rendered)
        self.assertNotIn("sentinel", str(DocumentSearchIndex.objects.first().combined_vector))

    def test_cold_command_refuses_wrong_corpus_size_before_truncate(self):
        make_document(reg_number="COLD-ONLY")
        before = DocumentSearchIndex.objects.count()

        with self.assertRaises(CommandError):
            call_command(
                "rebuild_search_index",
                cold=True,
                require_count=10000,
            )

        self.assertEqual(DocumentSearchIndex.objects.count(), before)

