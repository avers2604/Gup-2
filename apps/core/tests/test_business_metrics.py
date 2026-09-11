import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.business_metrics import record_link_generation_failure, record_search
from apps.core.models import OcrReviewQueueEntry
from apps.documents.models import NormativeDocument
from apps.documents.tests.factories import make_document
from apps.templates_bank.models import Template, TemplateFamily


class BusinessMetricsTests(TestCase):
    def test_exports_all_four_required_business_indicators(self):
        now = timezone.now()
        document = make_document(
            reg_number="METRIC-OCR",
            ocr_status=NormativeDocument.OcrStatus.NEEDS_REVIEW,
        )
        OcrReviewQueueEntry.objects.create(
            document_id=document.pk,
            required_at=now - datetime.timedelta(days=15),
        )

        approving = make_document(reg_number="METRIC-APPROVE")
        family = TemplateFamily.objects.create(name="Тестовая форма")
        Template.objects.create(
            family=family,
            version="v1.0",
            change_type=Template.ChangeType.MAJOR,
            approving_document=approving,
            file_editable="templates/editable/form.docx",
            file_sample="templates/samples/form.pdf",
            last_reviewed_at=(now - datetime.timedelta(days=365 * 4)).date(),
        )

        record_search(0, logical_search_key="metrics-zero")
        record_search(3, logical_search_key="metrics-hit")
        record_link_generation_failure(403)
        record_link_generation_failure(404)
        record_link_generation_failure(504)

        response = self.client.get(reverse("core:business-metrics"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("bz_get_ocr_review_overdue_total 1", body)
        self.assertIn("bz_get_templates_revision_overdue_total 1", body)
        self.assertIn("bz_get_search_requests_total 2", body)
        self.assertIn("bz_get_search_zero_results_total 1", body)
        self.assertIn("bz_get_search_zero_result_ratio 0.50000000", body)
        self.assertIn('bz_get_link_generation_failures_total{status="403"} 1', body)
        self.assertIn('bz_get_link_generation_failures_total{status="404"} 1', body)
        self.assertIn('bz_get_link_generation_failures_total{status="504"} 1', body)
        self.assertIn("pagination and short-window repeats are excluded", body)
        self.assertIn("business 404/409 states are excluded", body)

    def test_ocr_overdue_metric_requires_current_needs_review_status(self):
        now = timezone.now()
        document = make_document(
            reg_number="METRIC-OCR-DONE",
            ocr_status=NormativeDocument.OcrStatus.INDEXED,
        )
        OcrReviewQueueEntry.objects.create(
            document_id=document.pk,
            required_at=now - datetime.timedelta(days=30),
        )

        body = self.client.get(reverse("core:business-metrics")).content.decode()
        self.assertIn("bz_get_ocr_review_overdue_total 0", body)
