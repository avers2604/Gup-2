"""Рабочее место «Вычитка OCR».

Очередь ручной вычитки велась с Этапа 3, но разбирать её было негде. Здесь
проверяется не «страница открылась», а то, ради чего она заведена: документ
реально уходит из очереди, правка попадает в WORM-журнал, и ДСП не протекает
тому, у кого нет допуска.
"""
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.models import OcrReviewQueueEntry
from apps.documents import services
from apps.documents.models import NormativeDocument
from apps.iam.models import User

from .factories import make_document
from .test_permissions import PASSWORD, make_user


def _queue(document):
    return OcrReviewQueueEntry.objects.create(
        document_id=document.pk, required_at=timezone.now()
    )


class OcrReviewQueueViewTests(TestCase):
    def setUp(self):
        self.methodist = make_user(personnel_number="0500", role=User.Role.METHODIST)
        self.reader = make_user(personnel_number="0501")
        self.document = make_document(
            reg_number="OCR-1",
            ocr_status=NormativeDocument.OcrStatus.NEEDS_REVIEW,
            ocr_confidence=61.5,
            ocr_body="расnознанный тeкст с ошибкaми",
        )
        _queue(self.document)

    def _client(self, user):
        client = Client()
        client.login(personnel_number=user.personnel_number, password=PASSWORD)
        return client

    def test_requires_login(self):
        self.assertEqual(Client().get(reverse("documents:ocr_review_queue")).status_code, 302)

    def test_reader_does_not_see_the_workplace(self):
        response = self._client(self.reader).get(reverse("documents:ocr_review_queue"))
        self.assertEqual(response.status_code, 404)

    def test_methodist_sees_queued_document(self):
        response = self._client(self.methodist).get(reverse("documents:ocr_review_queue"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OCR-1")

    def test_dsp_document_is_hidden_without_clearance(self):
        """Очередь не должна выдавать даже факт существования ДСП-документа."""
        secret = make_document(
            reg_number="OCR-DSP",
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
            ocr_status=NormativeDocument.OcrStatus.NEEDS_REVIEW,
        )
        _queue(secret)

        response = self._client(self.methodist).get(reverse("documents:ocr_review_queue"))

        self.assertNotContains(response, "OCR-DSP")
        self.assertContains(response, "OCR-1")


class ApplyOcrReviewTests(TestCase):
    def setUp(self):
        self.methodist = make_user(personnel_number="0510", role=User.Role.METHODIST)
        self.reader = make_user(personnel_number="0511")
        self.document = make_document(
            reg_number="OCR-2",
            ocr_status=NormativeDocument.OcrStatus.NEEDS_REVIEW,
            ocr_confidence=58.0,
            ocr_body="плoхoй тeкст",
        )
        _queue(self.document)

    def test_review_indexes_document_and_clears_the_queue(self):
        services.apply_ocr_review(
            actor=self.methodist, document=self.document, corrected_text="хороший текст",
        )

        self.document.refresh_from_db()
        self.assertEqual(self.document.ocr_body, "хороший текст")
        self.assertEqual(self.document.ocr_status, NormativeDocument.OcrStatus.INDEXED)
        self.assertFalse(
            OcrReviewQueueEntry.objects.filter(document_id=self.document.pk).exists(),
            "документ обязан уйти из очереди, иначе метрика просрочки не закроется",
        )

    def test_machine_confidence_is_not_overwritten(self):
        """ocr_confidence — измерение машины, по нему настраиваются пороги."""
        services.apply_ocr_review(
            actor=self.methodist, document=self.document, corrected_text="текст",
        )

        self.document.refresh_from_db()
        self.assertEqual(self.document.ocr_confidence, 58.0)

    def test_review_is_recorded_in_worm_log_without_the_text_itself(self):
        services.apply_ocr_review(
            actor=self.methodist, document=self.document, corrected_text="исправленный текст",
        )

        entry = AuditLog.objects.filter(
            event_type=AuditLog.EventType.DOCUMENT_OCR_REVIEWED
        ).latest("created_at")
        self.assertEqual(entry.actor_personnel_number, "0510")
        self.assertEqual(entry.object_id, str(self.document.pk))
        self.assertEqual(entry.details["reg_number"], "OCR-2")
        self.assertEqual(entry.details["new_length"], len("исправленный текст"))
        # Сам текст в журнал не попадает: он бывает на сотни килобайт и может
        # содержать ДСП-содержимое, а журнал читают шире, чем сам документ.
        self.assertNotIn("исправленный текст", str(entry.details))

    def test_reader_cannot_review(self):
        from django.core.exceptions import PermissionDenied

        with self.assertRaises(PermissionDenied):
            services.apply_ocr_review(
                actor=self.reader, document=self.document, corrected_text="нельзя",
            )

        self.document.refresh_from_db()
        self.assertEqual(self.document.ocr_body, "плoхoй тeкст")

    def test_review_allowed_for_document_in_force_not_only_draft(self):
        """Плохое распознавание у действующего документа исправимо.

        Правка карточки разрешена только черновику, но ocr_body — не реквизит
        документа, а поисковый материал; запрет означал бы, что действующий
        документ навсегда останется ненаходимым.
        """
        self.document.status = NormativeDocument.Status.ACTIVE
        self.document.save(update_fields=["status"])

        services.apply_ocr_review(
            actor=self.methodist, document=self.document, corrected_text="норм",
        )

        self.document.refresh_from_db()
        self.assertEqual(self.document.ocr_body, "норм")


class OcrReviewFormViewTests(TestCase):
    def setUp(self):
        self.methodist = make_user(personnel_number="0520", role=User.Role.METHODIST)
        self.document = make_document(
            reg_number="OCR-3",
            ocr_status=NormativeDocument.OcrStatus.NEEDS_REVIEW,
            ocr_body="исхoдный",
        )
        _queue(self.document)
        self.client_ = Client()
        self.client_.login(personnel_number="0520", password=PASSWORD)

    def test_form_is_prefilled_with_recognised_text(self):
        response = self.client_.get(reverse("documents:ocr_review", args=[self.document.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "исхoдный")

    def test_post_saves_and_redirects_to_queue(self):
        response = self.client_.post(
            reverse("documents:ocr_review", args=[self.document.pk]),
            {"ocr_body": "вычитанный текст"},
        )

        self.assertRedirects(response, reverse("documents:ocr_review_queue"))
        self.document.refresh_from_db()
        self.assertEqual(self.document.ocr_body, "вычитанный текст")
        self.assertEqual(self.document.ocr_status, NormativeDocument.OcrStatus.INDEXED)

    def test_empty_text_is_accepted_as_deliberate_clearing(self):
        """Скан без читаемого текста — валидный исход вычитки, не ошибка."""
        response = self.client_.post(
            reverse("documents:ocr_review", args=[self.document.pk]), {"ocr_body": "   "},
        )

        self.assertRedirects(response, reverse("documents:ocr_review_queue"))
        self.document.refresh_from_db()
        self.assertEqual(self.document.ocr_body, "")
        self.assertEqual(self.document.ocr_status, NormativeDocument.OcrStatus.INDEXED)
