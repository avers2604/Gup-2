from unittest.mock import patch
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from apps.core.models import TaskOutbox
from apps.core.outbox import enqueue, dispatch_pending
from apps.documents.tests.factories import make_document


class OutboxTests(TestCase):
    def test_rollback_removes_delivery_intent(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                enqueue("apps.search_ocr.tasks.refresh_document_search_index", ["unused"])
                raise RuntimeError("rollback")
        self.assertFalse(TaskOutbox.objects.exists())

    def test_broker_failure_preserves_intent_and_retry_reuses_task_id(self):
        with patch("apps.search_ocr.tasks.refresh_document_search_index.apply_async", side_effect=ConnectionError):
            with self.captureOnCommitCallbacks(execute=True):
                entry = enqueue("apps.search_ocr.tasks.refresh_document_search_index", ["unused"])
        entry.refresh_from_db()
        self.assertIsNone(entry.delivered_at)
        self.assertEqual(entry.attempts, 1)
        TaskOutbox.objects.filter(pk=entry.pk).update(next_attempt_at=timezone.now())
        with patch("apps.search_ocr.tasks.refresh_document_search_index.apply_async") as send:
            self.assertEqual(dispatch_pending(), 1)
            self.assertEqual(dispatch_pending(), 0)
        send.assert_called_once_with(args=["unused"], task_id=str(entry.pk), retry=False)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_source_document_survives_broker_outage(self):
        with patch("apps.search_ocr.tasks.refresh_document_search_index.apply_async", side_effect=ConnectionError):
            with self.captureOnCommitCallbacks(execute=True):
                doc = make_document(reg_number="OUTBOX-1")
        self.assertTrue(type(doc).objects.filter(pk=doc.pk).exists())
        self.assertTrue(TaskOutbox.objects.filter(delivered_at__isnull=True, args=[str(doc.pk)]).exists())

    def test_pending_delivery_is_visible_in_metrics(self):
        from apps.core.business_metrics import render_prometheus
        TaskOutbox.objects.create(task_name="unused", args=[])
        rendered = render_prometheus()
        self.assertIn("bz_get_outbox_pending 1", rendered)
        self.assertIn("bz_get_outbox_oldest_seconds", rendered)
