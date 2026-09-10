import io

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase

from apps.documents.models import NormativeDocument
from apps.documents.tests.factories import make_document
from apps.iam.models import Department, User
from apps.search_ocr.indexed_search import search_documents_indexed
from apps.search_ocr.search_index import DocumentSearchIndex


class PersistedSearchIndexTests(TransactionTestCase):
    """Persistent index integration and bounded online rebuild."""

    reset_sequences = True

    def setUp(self):
        dept, _ = Department.objects.get_or_create(
            name="Служба поиска",
            defaults={"level": Department.Level.SERVICE},
        )
        self.user = User.objects.create(
            personnel_number="98001",
            last_name="Поисков",
            first_name="Тест",
            position="Тестировщик",
            department=dept,
            role=User.Role.READER,
        )

    def test_document_save_refreshes_persisted_index_in_eager_test_mode(self):
        document = make_document(
            reg_number="IDX-1",
            title="Регламент тяговой подстанции",
            status=NormativeDocument.Status.ACTIVE,
        )

        self.assertTrue(DocumentSearchIndex.objects.filter(document=document).exists())
        results = list(search_documents_indexed(self.user, "тяговой подстанции"))
        self.assertEqual([item.pk for item in results], [document.pk])

    def test_cold_rebuild_recreates_all_rows_and_reports_measurement(self):
        for number in range(3):
            make_document(
                reg_number=f"IDX-{number}",
                title=f"Регламент номер {number}",
                status=NormativeDocument.Status.ACTIVE,
            )
        DocumentSearchIndex.objects.all().delete()
        self.assertEqual(DocumentSearchIndex.objects.count(), 0)

        output = io.StringIO()
        call_command(
            "rebuild_search_index",
            batch_size=2,
            require_count=3,
            max_seconds=3600,
            as_json=True,
            stdout=output,
        )

        self.assertEqual(DocumentSearchIndex.objects.count(), 3)
        self.assertIn('"documents": 3', output.getvalue())
        self.assertIn('"result": "PASS"', output.getvalue())

    def test_cold_rebuild_rejects_wrong_acceptance_corpus_size(self):
        make_document(reg_number="IDX-ONLY")
        with self.assertRaises(CommandError):
            call_command("rebuild_search_index", require_count=10000)
