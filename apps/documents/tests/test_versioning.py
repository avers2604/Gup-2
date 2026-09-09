import datetime
import threading

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from ..models import DocumentRelation, DocumentStatusHistory, NormativeDocument
from ..services import relation_would_create_cycle
from .factories import make_document as _make_document


def _dt(*args):
    return timezone.datetime(*args, tzinfo=datetime.timezone.utc)


class DocumentStatusHistoryExclusionConstraintTests(TestCase):
    """SCD-2: периоды действия статуса одного документа не должны пересекаться (ТЗ 4.2.3)."""

    def test_touching_boundary_not_considered_overlap(self):
        # tstzrange по умолчанию полуинтервал [) — valid_to одного периода,
        # совпадающий с valid_from следующего, НЕ считается пересечением.
        doc = _make_document()
        t0, t1, t2 = _dt(2026, 1, 1), _dt(2026, 6, 1), _dt(2027, 1, 1)

        DocumentStatusHistory.objects.create(
            document=doc, status=NormativeDocument.Status.DRAFT, period=(t0, t1)
        )
        DocumentStatusHistory.objects.create(
            document=doc, status=NormativeDocument.Status.ACTIVE, period=(t1, t2)
        )
        self.assertEqual(doc.status_history.count(), 2)

    def test_overlapping_periods_rejected(self):
        doc = _make_document()
        t0, t2 = _dt(2026, 1, 1), _dt(2027, 1, 1)
        t_mid, t_later = _dt(2026, 6, 1), _dt(2027, 6, 1)

        DocumentStatusHistory.objects.create(
            document=doc, status=NormativeDocument.Status.DRAFT, period=(t0, t2)
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DocumentStatusHistory.objects.create(
                document=doc, status=NormativeDocument.Status.ACTIVE, period=(t_mid, t_later)
            )

    def test_overlapping_periods_allowed_for_different_documents(self):
        doc_a = _make_document(reg_number="142-п")
        doc_b = _make_document(reg_number="143-п")
        t0, t1 = _dt(2026, 1, 1), _dt(2027, 1, 1)

        DocumentStatusHistory.objects.create(
            document=doc_a, status=NormativeDocument.Status.ACTIVE, period=(t0, t1)
        )
        # Тот же период у другого документа — ограничение не должно сработать.
        DocumentStatusHistory.objects.create(
            document=doc_b, status=NormativeDocument.Status.ACTIVE, period=(t0, t1)
        )

    def test_open_ended_period_detects_overlap(self):
        # valid_to = NULL — статус действует «по настоящее время», верхняя
        # граница не задана (в Postgres это НЕ то же самое, что литерал
        # 'infinity', но для EXCLUDE-проверки пересечения ведёт себя так же:
        # всё, что начинается после valid_from, считается пересекающимся).
        doc = _make_document()
        t0 = _dt(2026, 1, 1)
        DocumentStatusHistory.objects.create(
            document=doc, status=NormativeDocument.Status.ACTIVE, period=(t0, None)
        )
        t_mid, t_later = _dt(2026, 6, 1), _dt(2026, 12, 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DocumentStatusHistory.objects.create(
                document=doc, status=NormativeDocument.Status.ACTIVE_AMENDED, period=(t_mid, t_later)
            )

    def test_open_ended_period_touching_boundary_allowed(self):
        doc = _make_document()
        t0, t1 = _dt(2026, 1, 1), _dt(2026, 6, 1)
        DocumentStatusHistory.objects.create(
            document=doc, status=NormativeDocument.Status.DRAFT, period=(t0, t1)
        )
        # Новый период стартует ровно там, где закончился предыдущий, и не
        # имеет верхней границы (текущий статус) — не пересечение.
        DocumentStatusHistory.objects.create(
            document=doc, status=NormativeDocument.Status.ACTIVE, period=(t1, None)
        )
        self.assertEqual(doc.status_history.count(), 2)


class DocumentRelationConstraintTests(TestCase):
    """Граф версионности DAG (ТЗ 4.2.2): без петель, дублей и циклов длиннее одного ребра.

    Самоссылка и дубль проверены дважды — через обычный .save()/.create()
    (ловит ValidationError на уровне Python, см. DocumentRelation.clean())
    и через сырой SQL в обход ORM целиком (ловит настоящий IntegrityError
    от ограничения в БД) — чтобы не полагаться только на Python-валидацию
    там, где есть constraint в БД. Раньше для DB-уровня использовался
    bulk_create(), но теперь он сам запрещён (см.
    DocumentRelationQuerySet.bulk_create в models.py) — сырой SQL здесь
    даже честнее: проверяет constraint независимо от того, что вообще
    умеет ORM."""

    def test_self_reference_rejected_via_save(self):
        doc = _make_document()
        with self.assertRaises(ValidationError):
            DocumentRelation.objects.create(
                from_document=doc, to_document=doc, relation_type=DocumentRelation.RelationType.CANCELS
            )

    def test_self_reference_rejected_at_db_level(self):
        doc = _make_document()
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO documents_documentrelation "
                    "(from_document_id, to_document_id, relation_type, note, created_at) "
                    "VALUES (%s, %s, %s, '', now())",
                    [doc.pk, doc.pk, DocumentRelation.RelationType.CANCELS],
                )

    def test_duplicate_relation_rejected_via_save(self):
        doc_a = _make_document(reg_number="142-п")
        doc_b = _make_document(reg_number="143-п")
        DocumentRelation.objects.create(
            from_document=doc_a, to_document=doc_b, relation_type=DocumentRelation.RelationType.CANCELS
        )
        with self.assertRaises(ValidationError):
            DocumentRelation.objects.create(
                from_document=doc_a, to_document=doc_b, relation_type=DocumentRelation.RelationType.CANCELS
            )

    def test_duplicate_relation_rejected_at_db_level(self):
        doc_a = _make_document(reg_number="142-п")
        doc_b = _make_document(reg_number="143-п")
        DocumentRelation.objects.create(
            from_document=doc_a, to_document=doc_b, relation_type=DocumentRelation.RelationType.CANCELS
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO documents_documentrelation "
                    "(from_document_id, to_document_id, relation_type, note, created_at) "
                    "VALUES (%s, %s, %s, '', now())",
                    [doc_a.pk, doc_b.pk, DocumentRelation.RelationType.CANCELS],
                )

    def test_bulk_create_is_blocked(self):
        # Правило задания: связи создаются только через .save()/.create()
        # (сервисный слой вызывает проверку цикличности) — прямой
        # bulk_create() запрещён, а не «работает, но не проверяет».
        doc_a = _make_document(reg_number="142-п")
        doc_b = _make_document(reg_number="143-п")
        relation = DocumentRelation(
            from_document=doc_a, to_document=doc_b, relation_type=DocumentRelation.RelationType.CANCELS
        )
        with self.assertRaises(NotImplementedError):
            DocumentRelation.objects.bulk_create([relation])
        self.assertEqual(DocumentRelation.objects.count(), 0)

    def test_indirect_cycle_of_three_rejected(self):
        # A -> B -> C уже есть; замыкание C -> A создаёт цикл длиной 3,
        # который ни CheckConstraint, ни UniqueConstraint не видят —
        # только обход графа (apps.documents.services).
        doc_a = _make_document(reg_number="142-п")
        doc_b = _make_document(reg_number="143-п")
        doc_c = _make_document(reg_number="144-п")
        DocumentRelation.objects.create(
            from_document=doc_a, to_document=doc_b, relation_type=DocumentRelation.RelationType.AMENDS
        )
        DocumentRelation.objects.create(
            from_document=doc_b, to_document=doc_c, relation_type=DocumentRelation.RelationType.AMENDS
        )
        with self.assertRaises(ValidationError):
            DocumentRelation.objects.create(
                from_document=doc_c, to_document=doc_a, relation_type=DocumentRelation.RelationType.AMENDS
            )
        self.assertEqual(DocumentRelation.objects.count(), 2)

    def test_non_cyclic_chain_allowed(self):
        doc_a = _make_document(reg_number="142-п")
        doc_b = _make_document(reg_number="143-п")
        doc_c = _make_document(reg_number="144-п")
        DocumentRelation.objects.create(
            from_document=doc_a, to_document=doc_b, relation_type=DocumentRelation.RelationType.AMENDS
        )
        DocumentRelation.objects.create(
            from_document=doc_b, to_document=doc_c, relation_type=DocumentRelation.RelationType.AMENDS
        )
        self.assertEqual(DocumentRelation.objects.count(), 2)

    def test_relation_would_create_cycle_helper_directly(self):
        doc_a = _make_document(reg_number="142-п")
        doc_b = _make_document(reg_number="143-п")
        self.assertFalse(relation_would_create_cycle(doc_a.pk, doc_b.pk))
        DocumentRelation.objects.create(
            from_document=doc_a, to_document=doc_b, relation_type=DocumentRelation.RelationType.REFERENCES
        )
        self.assertTrue(relation_would_create_cycle(doc_b.pk, doc_a.pk))


class ConcurrentStatusTransitionTests(TransactionTestCase):
    """Даже без явной блокировки строки документа (SELECT ... FOR UPDATE —
    см. предупреждение в docstring DocumentStatusHistory), EXCLUDE USING
    gist в БД не даёт двум параллельным транзакциям закоммитить
    пересекающиеся периоды: одна из них гарантированно получит
    IntegrityError. TestCase здесь не подходит — он оборачивает тест в одну
    транзакцию на одном соединении, что делает параллельность невозможной;
    нужен TransactionTestCase с реальными отдельными соединениями по потокам."""

    def test_concurrent_overlapping_inserts_only_one_succeeds(self):
        doc = _make_document()
        period = (_dt(2026, 1, 1), _dt(2026, 6, 1))
        results = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                barrier.wait(timeout=5)
                DocumentStatusHistory.objects.create(
                    document=doc, status=NormativeDocument.Status.ACTIVE, period=period
                )
                results.append("ok")
            except IntegrityError:
                results.append("integrity_error")
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sorted(results), ["integrity_error", "ok"])
        self.assertEqual(DocumentStatusHistory.objects.filter(document=doc).count(), 1)


class FileStorageRoutingTests(TestCase):
    """Оригиналы и редактируемые файлы должны идти в разные хранилища
    (STACK.md → «Разделение политик хранения MinIO»).

    Тесты не пишут файлы на диск/в бакет (сравнивают только объекты
    storage), поэтому очистки после себя не требуют и не рискуют задеть
    Object Lock на бакете originals."""

    def test_files_original_and_files_editable_use_different_storages(self):
        original_storage = NormativeDocument._meta.get_field("files_original").storage
        editable_storage = NormativeDocument._meta.get_field("files_editable").storage
        self.assertIsNot(original_storage, editable_storage)

    def test_storage_locations_differ_when_filesystem_backed(self):
        from django.core.files.storage import FileSystemStorage

        original_storage = NormativeDocument._meta.get_field("files_original").storage
        editable_storage = NormativeDocument._meta.get_field("files_editable").storage
        if isinstance(original_storage, FileSystemStorage) and isinstance(editable_storage, FileSystemStorage):
            self.assertNotEqual(str(original_storage.location), str(editable_storage.location))
