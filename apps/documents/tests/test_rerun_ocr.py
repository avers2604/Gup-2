"""Тесты management-команды rerun_ocr — ретроактивная постановка OCR в
очередь. run_ocr_for_document.delay замокан: команда проверяется только на
то, какие документы она отбирает и сколько задач ставит, реальный прогон
задачи уже покрыт test_ocr_task.py."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.core.models import TaskOutbox
from apps.documents.models import NormativeDocument

from .factories import make_document


class RerunOcrCommandTests(TestCase):
    def test_default_only_queues_not_processed_documents_with_a_file(self):
        not_processed = make_document(
            reg_number="RQ-1", files_original="documents/originals/2026/01/rq1.pdf",
            ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED,
        )
        make_document(
            reg_number="RQ-2", files_original="documents/originals/2026/01/rq2.pdf",
            ocr_status=NormativeDocument.OcrStatus.INDEXED,
        )
        make_document(reg_number="RQ-3", ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED)

        with patch("apps.documents.management.commands.rerun_ocr.run_ocr_for_document.delay") as mock_delay:
            call_command("rerun_ocr")

        mock_delay.assert_called_once_with(str(not_processed.pk))

    def test_force_queues_already_processed_documents_too(self):
        not_processed = make_document(
            reg_number="RQ-4", files_original="documents/originals/2026/01/rq4.pdf",
            ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED,
        )
        indexed = make_document(
            reg_number="RQ-5", files_original="documents/originals/2026/01/rq5.pdf",
            ocr_status=NormativeDocument.OcrStatus.INDEXED,
        )

        with patch("apps.documents.management.commands.rerun_ocr.run_ocr_for_document.delay") as mock_delay:
            call_command("rerun_ocr", "--force")

        queued_ids = {call.args[0] for call in mock_delay.call_args_list}
        self.assertEqual(queued_ids, {str(not_processed.pk), str(indexed.pk)})

    def test_documents_without_a_file_are_never_queued(self):
        make_document(reg_number="RQ-6", ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED)

        with patch("apps.documents.management.commands.rerun_ocr.run_ocr_for_document.delay") as mock_delay:
            call_command("rerun_ocr", "--force")

        mock_delay.assert_not_called()

    def test_limit_caps_number_of_queued_documents(self):
        for i in range(5):
            make_document(
                reg_number=f"RQ-LIM-{i}", files_original=f"documents/originals/2026/01/rq-lim-{i}.pdf",
                ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED,
            )

        with patch("apps.documents.management.commands.rerun_ocr.run_ocr_for_document.delay") as mock_delay:
            call_command("rerun_ocr", "--limit=2")

        self.assertEqual(mock_delay.call_count, 2)

    def test_prints_queued_count(self):
        make_document(
            reg_number="RQ-7", files_original="documents/originals/2026/01/rq7.pdf",
            ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED,
        )
        out = StringIO()

        with patch("apps.documents.management.commands.rerun_ocr.run_ocr_for_document.delay"):
            call_command("rerun_ocr", stdout=out)

        self.assertIn("Поставлено в очередь: 1", out.getvalue())

    def test_rerun_persists_durable_outbox_intent_before_delivery(self):
        """Ручной rerun не должен теряться при недоступном брокере Redis."""
        document = make_document(
            reg_number="RQ-DURABLE",
            ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED,
        )
        # Добавляем имя оригинала в обход model.save(): иначе сама модель
        # штатно создаст OCR outbox при первой загрузке файла и тест не сможет
        # доказать, что durable intent создала именно management-команда.
        NormativeDocument.objects.filter(pk=document.pk).update(
            files_original="documents/originals/2026/01/rq-durable.pdf",
        )

        with patch("apps.documents.management.commands.rerun_ocr.run_ocr_for_document.delay"):
            call_command("rerun_ocr")

        entry = TaskOutbox.objects.get(
            task_name="apps.documents.tasks.run_ocr_for_document",
            args=[str(document.pk)],
        )
        self.assertIsNone(entry.delivered_at)
