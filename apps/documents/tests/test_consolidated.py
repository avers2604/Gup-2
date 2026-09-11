"""Сводные редакции (пункт меню 8.3 решения Заказчика).

Трактовка пункта — допущение, зафиксированное в `apps/documents/consolidated.py`
и в STACK.md. Тесты закрепляют именно её: если Заказчик уточнит смысл
«сводной редакции», меняться должны они вместе с трактовкой.
"""
import datetime

from django.test import Client, TestCase
from django.urls import reverse

from apps.documents.models import DocumentRelation, NormativeDocument

from .factories import make_document
from .test_permissions import PASSWORD, make_user


def amend(source, target, note=""):
    return DocumentRelation.objects.create(
        from_document=source,
        to_document=target,
        relation_type=DocumentRelation.RelationType.AMENDS,
        note=note,
    )


class ConsolidatedListTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0700")
        self.client_ = Client()
        self.client_.login(personnel_number="0700", password=PASSWORD)

        self.base = make_document(
            reg_number="БАЗА-1", status=NormativeDocument.Status.ACTIVE_AMENDED
        )
        self.amendment = make_document(
            reg_number="ИЗМ-1", status=NormativeDocument.Status.ACTIVE
        )
        amend(self.amendment, self.base, note="пункты 4.2, 4.3")

    def test_requires_login(self):
        self.assertEqual(Client().get(reverse("documents:consolidated_list")).status_code, 302)

    def test_amended_document_is_listed(self):
        response = self.client_.get(reverse("documents:consolidated_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "БАЗА-1")

    def test_document_without_amendments_is_not_listed(self):
        make_document(reg_number="ОДИНОЧКА", status=NormativeDocument.Status.ACTIVE)

        response = self.client_.get(reverse("documents:consolidated_list"))

        self.assertNotContains(response, "ОДИНОЧКА")

    def test_revoked_amendment_drops_out_of_the_summary(self):
        """Утратившее силу изменение больше ничего не меняет.

        Показывать его как действующее — вводить в заблуждение того, кто
        пришёл узнать актуальное требование.
        """
        self.amendment.status = NormativeDocument.Status.REVOKED
        self.amendment.save(update_fields=["status"])

        response = self.client_.get(reverse("documents:consolidated_list"))

        # Проверка по контексту, а не по тексту страницы: сводить у
        # документа стало нечего, но сам он остаётся на экране — в блоке
        # расхождений статуса, где ему теперь и место.
        self.assertNotIn(self.base, list(response.context["documents"]))

    def test_draft_amendment_does_not_count(self):
        """Неизданный черновик изменением ещё не является."""
        self.amendment.status = NormativeDocument.Status.DRAFT
        self.amendment.save(update_fields=["status"])

        response = self.client_.get(reverse("documents:consolidated_list"))

        self.assertNotIn(self.base, list(response.context["documents"]))

    def test_stale_status_is_surfaced_separately(self):
        """«Действует с изм.» без единого действующего изменения.

        Переход обратно в «Действует» делает человек, и до него статус
        расходится с графом связей. Расхождение должно быть видно.
        """
        self.amendment.status = NormativeDocument.Status.ANNULLED
        self.amendment.save(update_fields=["status"])

        response = self.client_.get(reverse("documents:consolidated_list"))

        self.assertContains(response, "Статус разошёлся со связями")
        self.assertContains(response, "БАЗА-1")

    def test_other_relation_types_are_not_amendments(self):
        """«Ссылается на» и «Принят взамен» сводную редакцию не образуют."""
        base = make_document(reg_number="БАЗА-2", status=NormativeDocument.Status.ACTIVE)
        referrer = make_document(reg_number="ССЫЛКА-1", status=NormativeDocument.Status.ACTIVE)
        DocumentRelation.objects.create(
            from_document=referrer,
            to_document=base,
            relation_type=DocumentRelation.RelationType.REFERENCES,
        )

        response = self.client_.get(reverse("documents:consolidated_list"))

        self.assertNotContains(response, "БАЗА-2")


class ConsolidatedDetailTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0710")
        self.client_ = Client()
        self.client_.login(personnel_number="0710", password=PASSWORD)

        self.base = make_document(
            reg_number="БАЗА-10", status=NormativeDocument.Status.ACTIVE_AMENDED
        )
        self.late = make_document(
            reg_number="ИЗМ-ПОЗЖЕ",
            status=NormativeDocument.Status.ACTIVE,
            effective_date=datetime.date(2026, 6, 1),
        )
        self.early = make_document(
            reg_number="ИЗМ-РАНЬШЕ",
            status=NormativeDocument.Status.ACTIVE,
            effective_date=datetime.date(2026, 3, 1),
        )
        amend(self.late, self.base, note="пункт 7")
        amend(self.early, self.base, note="пункт 2")

    def _get(self, document=None):
        return self.client_.get(
            reverse("documents:consolidated_detail", args=[(document or self.base).pk])
        )

    def test_amendments_are_ordered_chronologically(self):
        """Читать сводку имеет смысл в порядке наложения изменений."""
        body = self._get().content.decode()

        self.assertLess(body.index("ИЗМ-РАНЬШЕ"), body.index("ИЗМ-ПОЗЖЕ"))

    def test_affected_clauses_are_shown(self):
        """`note` — часто единственное знание о том, что именно затронуто."""
        response = self._get()

        self.assertContains(response, "пункт 7")
        self.assertContains(response, "пункт 2")

    def test_page_states_it_is_not_a_merged_text(self):
        """Граница обязана быть на экране, а не только в документации.

        Система хранит сканы, а не структуру пунктов, и «сшить» текст не
        может. Пользователь, ожидающий готовый консолидированный документ,
        должен узнать об этом здесь.
        """
        self.assertContains(self._get(), "не единый текст документа")

    def test_dsp_amendment_is_hidden_without_clearance(self):
        secret = make_document(
            reg_number="ИЗМ-ДСП",
            status=NormativeDocument.Status.ACTIVE,
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        amend(secret, self.base)

        response = self._get()

        self.assertNotContains(response, "ИЗМ-ДСП")
        self.assertContains(response, "ИЗМ-РАНЬШЕ")

    def test_dsp_base_document_is_not_reachable_without_clearance(self):
        secret_base = make_document(
            reg_number="БАЗА-ДСП",
            status=NormativeDocument.Status.ACTIVE_AMENDED,
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )

        self.assertEqual(self._get(secret_base).status_code, 404)

    def test_user_with_clearance_sees_restricted_amendment(self):
        secret = make_document(
            reg_number="ИЗМ-ДСП-2",
            status=NormativeDocument.Status.ACTIVE,
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        amend(secret, self.base)
        cleared = make_user(personnel_number="0711", dsp_access=True)
        client = Client()
        client.login(personnel_number="0711", password=PASSWORD)

        response = client.get(
            reverse("documents:consolidated_detail", args=[self.base.pk])
        )

        self.assertContains(response, "ИЗМ-ДСП-2")
