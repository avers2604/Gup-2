from django.test import TestCase

from apps.documents.models import NormativeDocument
from apps.documents.tests.factories import make_document
from apps.iam.models import Department, User

from ..read_models import DocumentSearchIndex
from ..search import search_documents


class DocumentSearchReadModelTests(TestCase):
    def setUp(self):
        department, _ = Department.objects.get_or_create(
            name="Служба движения",
            defaults={"level": Department.Level.SERVICE},
        )
        self.user = User.objects.create(
            personnel_number="read-index-1",
            last_name="Иванов",
            first_name="Пётр",
            position="Водитель",
            department=department,
            role=User.Role.READER,
        )

    def test_document_save_populates_persisted_vectors(self):
        document = make_document(
            reg_number="READ-1",
            title="Регламент испытаний тяговой подстанции",
            status=NormativeDocument.Status.ACTIVE,
        )

        index = DocumentSearchIndex.objects.get(document=document)
        self.assertEqual(index.title, document.title)
        self.assertIsNotNone(index.title_vector)
        self.assertIsNotNone(index.search_vector)

    def test_search_uses_refreshed_read_model(self):
        document = make_document(
            reg_number="READ-2",
            title="Исходное наименование документа",
            status=NormativeDocument.Status.ACTIVE,
        )
        document.title = "Инструкция по ремонту контактной сети"
        document.save(update_fields=["title"])

        results = list(search_documents(self.user, "контактной сети"))
        self.assertEqual([item.pk for item in results], [document.pk])
        self.assertEqual(
            DocumentSearchIndex.objects.get(document=document).title,
            document.title,
        )
