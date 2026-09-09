import datetime

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Department

from .models import DocumentRelation, DocumentStatusHistory, NormativeDocument


def _make_document(reg_number="142-п", **kwargs):
    dept, _ = Department.objects.get_or_create(
        name="Служба движения", defaults={"level": Department.Level.SERVICE}
    )
    defaults = dict(
        reg_number=reg_number,
        reg_date=datetime.date(2026, 1, 1),
        effective_date=datetime.date(2026, 1, 2),
        doc_type=NormativeDocument.DocType.ORDER,
        title=f"Тестовый документ {reg_number}",
        issuer_dept=dept,
    )
    defaults.update(kwargs)
    return NormativeDocument.objects.create(**defaults)


class DocumentStatusHistoryExclusionConstraintTests(TestCase):
    """SCD-2: периоды действия статуса одного документа не должны пересекаться (ТЗ 4.2.3)."""

    def test_non_overlapping_periods_allowed(self):
        doc = _make_document()
        t0 = timezone.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        t1 = timezone.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)
        t2 = timezone.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc)

        DocumentStatusHistory.objects.create(
            document=doc, status=NormativeDocument.Status.DRAFT, period=(t0, t1)
        )
        DocumentStatusHistory.objects.create(
            document=doc, status=NormativeDocument.Status.ACTIVE, period=(t1, t2)
        )
        self.assertEqual(doc.status_history.count(), 2)

    def test_overlapping_periods_rejected(self):
        doc = _make_document()
        t0 = timezone.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        t2 = timezone.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc)
        t_mid = timezone.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)
        t_later = timezone.datetime(2027, 6, 1, tzinfo=datetime.timezone.utc)

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
        t0 = timezone.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        t1 = timezone.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc)

        DocumentStatusHistory.objects.create(
            document=doc_a, status=NormativeDocument.Status.ACTIVE, period=(t0, t1)
        )
        # Тот же период у другого документа — ограничение не должно сработать.
        DocumentStatusHistory.objects.create(
            document=doc_b, status=NormativeDocument.Status.ACTIVE, period=(t0, t1)
        )


class DocumentRelationConstraintTests(TestCase):
    """Граф версионности DAG (ТЗ 4.2.2): без петель и дублей одного типа связи."""

    def test_self_reference_rejected(self):
        doc = _make_document()
        with self.assertRaises(IntegrityError), transaction.atomic():
            DocumentRelation.objects.create(
                from_document=doc, to_document=doc, relation_type=DocumentRelation.RelationType.CANCELS
            )

    def test_duplicate_relation_rejected(self):
        doc_a = _make_document(reg_number="142-п")
        doc_b = _make_document(reg_number="143-п")
        DocumentRelation.objects.create(
            from_document=doc_a, to_document=doc_b, relation_type=DocumentRelation.RelationType.CANCELS
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DocumentRelation.objects.create(
                from_document=doc_a, to_document=doc_b, relation_type=DocumentRelation.RelationType.CANCELS
            )


class FileStorageRoutingTests(TestCase):
    """Оригиналы и редактируемые файлы должны идти в разные хранилища
    (STACK.md → «Разделение политик хранения MinIO»)."""

    def test_files_original_and_files_editable_use_different_storages(self):
        original_storage = NormativeDocument._meta.get_field("files_original").storage
        editable_storage = NormativeDocument._meta.get_field("files_editable").storage
        self.assertIsNot(original_storage, editable_storage)
