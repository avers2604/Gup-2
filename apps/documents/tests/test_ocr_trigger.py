"""Тесты точки запуска конвейера OCR — NormativeDocument.save() должен
поставить run_ocr_for_document в очередь через transaction.on_commit()
при первой загрузке/замене files_original, и не делать этого при
сохранении карточки без реального изменения скана.

captureOnCommitCallbacks — сами on_commit-колбэки не выполняются внутри
TestCase (оборачивающая транзакция никогда не коммитится), поэтому это
единственный штатный способ Django проверить, что on_commit() был вызван
с нужным аргументом."""
from unittest.mock import patch

from django.test import TestCase

from .factories import make_document


class OcrTriggerOnSaveTests(TestCase):
    def test_creating_document_with_file_enqueues_ocr(self):
        with patch("apps.documents.tasks.run_ocr_for_document.delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                doc = make_document(
                    reg_number="TRIG-1", files_original="documents/originals/2026/01/trig-1.pdf",
                )
        mock_delay.assert_called_once_with(str(doc.pk))

    def test_creating_document_without_file_does_not_enqueue_ocr(self):
        with patch("apps.documents.tasks.run_ocr_for_document.delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                make_document(reg_number="TRIG-2")
        mock_delay.assert_not_called()

    def test_resaving_without_changing_file_does_not_reenqueue_ocr(self):
        doc = make_document(reg_number="TRIG-3", files_original="documents/originals/2026/01/trig-3.pdf")

        with patch("apps.documents.tasks.run_ocr_for_document.delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                doc.title = "Обновлённый заголовок"
                doc.save()
        mock_delay.assert_not_called()

    def test_replacing_file_reenqueues_ocr(self):
        doc = make_document(reg_number="TRIG-4", files_original="documents/originals/2026/01/trig-4.pdf")

        with patch("apps.documents.tasks.run_ocr_for_document.delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                doc.files_original = "documents/originals/2026/01/trig-4-v2.pdf"
                doc.save()
        mock_delay.assert_called_once_with(str(doc.pk))

    def test_rolled_back_transaction_never_enqueues_ocr(self):
        # on_commit — а не прямой вызов .delay() внутри save() — именно
        # чтобы задача не стартовала раньше, чем строка реально
        # закоммичена и видна воркеру с отдельным соединением.
        with patch("apps.documents.tasks.run_ocr_for_document.delay") as mock_delay:
            try:
                with self.captureOnCommitCallbacks(execute=True):
                    from django.db import transaction

                    with transaction.atomic():
                        make_document(
                            reg_number="TRIG-5", files_original="documents/originals/2026/01/trig-5.pdf",
                        )
                        raise ValueError("симуляция отката")
            except ValueError:
                pass
        mock_delay.assert_not_called()
