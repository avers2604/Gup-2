"""Web GUI рабочих мест — реестр и карточка НРД (ТЗ 4.1)."""
import datetime

from django.test import Client, TestCase
from django.urls import reverse

from apps.iam.models import User

from ..models import DocumentRelation, NormativeDocument
from .factories import make_document
from .test_permissions import make_user

PASSWORD = "Sup3r$ecret!Pass"


class DocumentListViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = make_user()
        self.client.login(personnel_number=self.user.personnel_number, password=PASSWORD)
        self.document = make_document(reg_number="100-п", title="Документ о движении")

    def test_requires_login(self):
        response = Client().get(reverse("documents:list"))
        self.assertEqual(response.status_code, 302)

    def test_lists_document(self):
        response = self.client.get(reverse("documents:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "100-п")

    def test_restricted_document_hidden_without_clearance(self):
        make_document(
            reg_number="101-дсп", title="Секретный документ",
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        response = self.client.get(reverse("documents:list"))
        self.assertNotContains(response, "101-дсп")

    def test_restricted_document_visible_with_clearance(self):
        make_document(
            reg_number="101-дсп", access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        cleared = make_user(personnel_number="0002", dsp_access=True)
        client = Client()
        client.login(personnel_number=cleared.personnel_number, password=PASSWORD)
        response = client.get(reverse("documents:list"))
        self.assertContains(response, "101-дсп")

    def test_filter_by_doc_type(self):
        make_document(
            reg_number="102-р", doc_type=NormativeDocument.DocType.DIRECTIVE,
        )
        response = self.client.get(
            reverse("documents:list"), {"doc_type": NormativeDocument.DocType.DIRECTIVE}
        )
        self.assertContains(response, "102-р")
        self.assertNotContains(response, "100-п")

    def test_filter_by_effective_period(self):
        make_document(reg_number="103-п", effective_date=datetime.date(2030, 5, 1))
        response = self.client.get(
            reverse("documents:list"), {"effective_from": "2030-01-01"}
        )
        self.assertContains(response, "103-п")
        self.assertNotContains(response, "100-п")

    def test_reversed_period_reports_error_instead_of_empty_list(self):
        response = self.client.get(
            reverse("documents:list"),
            {"effective_from": "2030-01-01", "effective_to": "2020-01-01"},
        )
        self.assertContains(response, "Начало периода позже его окончания.")


class DocumentDetailViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = make_user()
        self.client.login(personnel_number=self.user.personnel_number, password=PASSWORD)
        self.document = make_document(reg_number="200-п", title="Основной документ")

    def test_shows_attributes(self):
        response = self.client.get(reverse("documents:detail", args=[self.document.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "200-п")
        self.assertContains(response, "Основной документ")

    def test_restricted_returns_404_not_403(self):
        # 403 подтвердил бы существование документа — тот же принцип
        # неразличимости ответов, что и у формы входа.
        restricted = make_document(
            reg_number="201-дсп", access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        response = self.client.get(reverse("documents:detail", args=[restricted.pk]))
        self.assertEqual(response.status_code, 404)

    def test_shows_outgoing_relation(self):
        other = make_document(reg_number="202-п", title="Отменяемый документ")
        DocumentRelation.objects.create(
            from_document=self.document, to_document=other,
            relation_type=DocumentRelation.RelationType.CANCELS,
        )
        response = self.client.get(reverse("documents:detail", args=[self.document.pk]))
        self.assertContains(response, "202-п")
        self.assertContains(response, "Отменяет")

    def test_restricted_related_document_hidden_without_clearance(self):
        # Иначе гриф обходится через граф связей соседнего документа.
        secret = make_document(
            reg_number="203-дсп", access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        DocumentRelation.objects.create(
            from_document=self.document, to_document=secret,
            relation_type=DocumentRelation.RelationType.REFERENCES,
        )
        response = self.client.get(reverse("documents:detail", args=[self.document.pk]))
        self.assertNotContains(response, "203-дсп")

    def test_action_hint_hidden_for_reader(self):
        response = self.client.get(reverse("documents:detail", args=[self.document.pk]))
        self.assertNotContains(response, "Доступные действия")

    def test_action_hint_shown_for_controller(self):
        controller = make_user(personnel_number="0003", role=User.Role.CONTROLLER_LAWYER)
        client = Client()
        client.login(personnel_number=controller.personnel_number, password=PASSWORD)
        response = client.get(reverse("documents:detail", args=[self.document.pk]))
        self.assertContains(response, "Доступные действия")


class TemplateCommentRenderingTests(TestCase):
    """Регрессия: `{# … #}` в Django однострочный. Многострочный
    комментарий этим синтаксисом НЕ вырезается — он попадает в ответ как
    видимый текст. Так в шапке (`templates/base.html`) несколько релизов
    висели служебные заметки про SRI-хэши и роль в личном кабинете:
    страницы отдавали 200, все assertContains проходили, и увидеть это
    можно было только глазами.

    Проверяются страницы, а не файлы шаблонов: важно, что артефакт не
    доходит до пользователя, каким бы синтаксисом комментарий ни был
    записан.
    """

    def setUp(self):
        self.client = Client()
        self.user = make_user()
        self.client.login(personnel_number=self.user.personnel_number, password=PASSWORD)
        make_document(reg_number="300-п")

    def test_pages_contain_no_raw_template_comments(self):
        for name, args in (
            ("documents:list", []),
            ("documents:detail", [make_document(reg_number="301-п").pk]),
        ):
            with self.subTest(page=name):
                body = self.client.get(reverse(name, args=args)).content.decode()
                self.assertNotIn("{#", body)
                self.assertNotIn("{% comment", body)
