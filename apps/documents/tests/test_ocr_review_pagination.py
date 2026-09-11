from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.core.models import OcrReviewQueueEntry
from apps.documents.models import NormativeDocument
from apps.iam.models import User

from .factories import make_document
from .test_permissions import PASSWORD, make_user


class OcrReviewQueuePaginationTests(TestCase):
    """Очередь должна резаться в БД до загрузки документов текущей страницы."""

    def setUp(self):
        self.methodist = make_user(
            personnel_number="0530", role=User.Role.METHODIST
        )
        self.client_ = Client()
        self.client_.login(
            personnel_number=self.methodist.personnel_number, password=PASSWORD
        )

        for index in range(30):
            document = make_document(
                reg_number=f"OCR-PAGE-{index:02d}",
                ocr_status=NormativeDocument.OcrStatus.NEEDS_REVIEW,
            )
            OcrReviewQueueEntry.objects.create(
                document_id=document.pk, required_at=timezone.now()
            )

        # Скрытый ДСП-документ не должен участвовать ни в строках, ни в count
        # пагинатора — иначе сам счётчик страниц раскрывает его существование.
        restricted = make_document(
            reg_number="OCR-PAGE-DSP",
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
            ocr_status=NormativeDocument.OcrStatus.NEEDS_REVIEW,
        )
        OcrReviewQueueEntry.objects.create(
            document_id=restricted.pk, required_at=timezone.now()
        )

    def test_database_limits_queue_before_materialising_page(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client_.get(reverse("documents:ocr_review_queue"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 30)
        self.assertEqual(len(response.context["entries"]), 25)
        self.assertNotContains(response, "OCR-PAGE-DSP")

        queue_reads = []
        for query in queries.captured_queries:
            sql = query["sql"].upper()
            if (
                "CORE_OCRREVIEWQUEUEENTRY" in sql
                and "DOCUMENT_ID" in sql
                and "REQUIRED_AT" in sql
                and "COUNT(" not in sql
            ):
                queue_reads.append(sql)

        self.assertTrue(queue_reads, "ожидался SELECT строк очереди OCR")
        self.assertTrue(
            any("LIMIT 25" in sql for sql in queue_reads),
            "строки очереди должны ограничиваться PAGE_SIZE на уровне SQL, "
            f"получены запросы: {queue_reads}",
        )
