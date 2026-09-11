"""Тесты management-команды rerun_ocr — durable постановка OCR в очередь.

Штатная загрузка оригинала уже использует apps.core.outbox: intent хранится
в PostgreSQL и не теряется при недоступном Redis. Эти тесты проверяют тот же
контракт для ручного rerun, а не внутренний вызов Celery `.delay()`.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.core.models import TaskOutbox
from apps.documents.models import NormativeDocument

from .factories import make_document


TASK_NAME = "apps.documents.tasks.run_ocr_for_document"


def _attach_original(document, filename):
    """Добавить имя файла без model.save() и его автоматического OCR outbox."""
    NormativeDocument.objects.filter(pk=document.pk).update(files_original=filename)
    return document


def _queued_document_ids():
    return {
        args[0]
        for args in TaskOutbox.objects.filter(task_name=TASK_NAME).values_list("args", flat=True)
    }


class RerunOcrCommandTests(TestCase):
    def test_default_only_queues_not_processed_documents_with_a_file(self):
        not_processed = _attach_original(
            make_document(reg_number="RQ-1", ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED),
            "documents/originals/2026/01/rq1.pdf",
        )
        _attach_original(
            make_document(reg_number="RQ-2", ocr_status=NormativeDocument.OcrStatus.INDEXED),
            "documents/originals/2026/01/rq2.pdf",
        )
        make_document(reg_number="RQ-3", ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED)

        call_command("rerun_ocr")

        self.assertEqual(_queued_document_ids(), {str(not_processed.pk)})

    def test_force_queues_already_processed_documents_too(self):
        not_processed = _attach_original(
            make_document(reg_number="RQ-4", ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED),
            "documents/originals/2026/01/rq4.pdf",
        )
        indexed = _attach_original(
            make_document(reg_number="RQ-5", ocr_status=NormativeDocument.OcrStatus.INDEXED),
            "documents/originals/2026/01/rq5.pdf",
        )

        call_command("rerun_ocr", "--force")

        self.assertEqual(_queued_document_ids(), {str(not_processed.pk), str(indexed.pk)})

    def test_documents_without_a_file_are_never_queued(self):
        make_document(reg_number="RQ-6", ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED)

        call_command("rerun_ocr", "--force")

        self.assertFalse(TaskOutbox.objects.filter(task_name=TASK_NAME).exists())

    def test_limit_caps_number_of_queued_documents(self):
        for i in range(5):
            _attach_original(
                make_document(
                    reg_number=f"RQ-LIM-{i}",
                    ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED,
                ),
                f"documents/originals/2026/01/rq-lim-{i}.pdf",
            )

        call_command("rerun_ocr", "--limit=2")

        self.assertEqual(TaskOutbox.objects.filter(task_name=TASK_NAME).count(), 2)

    def test_prints_queued_count(self):
        _attach_original(
            make_document(reg_number="RQ-7", ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED),
            "documents/originals/2026/01/rq7.pdf",
        )
        out = StringIO()

        call_command("rerun_ocr", stdout=out)

        self.assertIn("Поставлено в очередь: 1", out.getvalue())

    def test_rerun_persists_durable_outbox_intent_before_delivery(self):
        """Ручной rerun не должен теряться при недоступном брокере Redis."""
        document = _attach_original(
            make_document(
                reg_number="RQ-DURABLE",
                ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED,
            ),
            "documents/originals/2026/01/rq-durable.pdf",
        )

        call_command("rerun_ocr")

        entry = TaskOutbox.objects.get(
            task_name=TASK_NAME,
            args=[str(document.pk)],
        )
        # TestCase держит внешнюю транзакцию, поэтому on_commit-dispatch ещё
        # не выполнялся: можно доказать, что durable intent существует ДО
        # фактической доставки в Celery.
        self.assertIsNone(entry.delivered_at)
