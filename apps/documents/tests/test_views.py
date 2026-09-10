"""Web GUI рабочих мест — реестр и карточка НРД (ТЗ 4.1)."""
import datetime

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from apps.core.tests.clamd_fixture import ClamdTestCase
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


class _WriteTestSetup:
    """Общая обвязка тестов записи. Именно миксин, а не общий базовый
    TestCase: наследование одного класса тестов от другого прогнало бы
    весь его набор повторно — в данном случае ещё и под живым clamd,
    который поднимается на класс."""

    def setUp(self):
        super().setUp()
        self.methodist = make_user(personnel_number="0100", role=User.Role.METHODIST)
        self.controller = make_user(personnel_number="0101", role=User.Role.CONTROLLER_LAWYER)
        self.reader = make_user(personnel_number="0102")
        self.document = make_document(
            reg_number="400-п", files_original="documents/originals/2026/01/scan.pdf",
        )

    def _client(self, user):
        client = Client()
        client.login(personnel_number=user.personnel_number, password=PASSWORD)
        return client

    def _form_data(self, **overrides):
        data = {
            "reg_number": "401-п", "reg_date": "2026-04-01", "effective_date": "2026-04-10",
            "doc_type": NormativeDocument.DocType.ORDER, "title": "Созданный через форму",
            "summary": "", "issuer_dept": self.document.issuer_dept_id,
            "access_level": NormativeDocument.AccessLevel.GENERAL,
            "retention_category": self.document.retention_category,
            "ocr_category": "", "revision": self.document.edit_version,
        }
        data.update(overrides)
        return data


class DocumentWriteViewTests(_WriteTestSetup, TestCase):
    """Web GUI записи: регистрация, правка, смена статуса (ТЗ 4.1)."""

    # --- регистрация ---

    def test_reader_does_not_see_create_page(self):
        response = self._client(self.reader).get(reverse("documents:create"))
        self.assertEqual(response.status_code, 404)

    def test_effective_date_before_reg_date_rejected(self):
        scan = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        response = self._client(self.methodist).post(
            reverse("documents:create"),
            self._form_data(effective_date="2026-01-01", files_original=scan),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "вступить в силу раньше")
        self.assertFalse(NormativeDocument.objects.filter(reg_number="401-п").exists())

    def test_declassification_date_without_dsp_rejected(self):
        scan = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        response = self._client(self.methodist).post(
            reverse("documents:create"),
            self._form_data(declassification_date="2030-01-01", files_original=scan),
        )
        self.assertContains(response, "только для документов с грифом")

    # --- правка ---

    def test_reader_cannot_open_edit_page(self):
        response = self._client(self.reader).get(
            reverse("documents:edit", args=[self.document.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_edit_page_closed_for_document_in_force(self):
        active = make_document(
            reg_number="402-п", status=NormativeDocument.Status.ACTIVE,
            files_original="documents/originals/2026/01/scan.pdf",
        )
        response = self._client(self.methodist).get(reverse("documents:edit", args=[active.pk]))
        self.assertEqual(response.status_code, 404)

    def test_methodist_edits_draft(self):
        response = self._client(self.methodist).post(
            reverse("documents:edit", args=[self.document.pk]),
            self._form_data(reg_number="400-п", title="Исправленное наименование"),
        )
        self.assertEqual(response.status_code, 302)
        self.document.refresh_from_db()
        self.assertEqual(self.document.title, "Исправленное наименование")

    # --- смена статуса ---

    def test_methodist_cannot_open_status_page(self):
        response = self._client(self.methodist).get(
            reverse("documents:status", args=[self.document.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_status_page_offers_only_allowed_transitions(self):
        response = self._client(self.controller).get(
            reverse("documents:status", args=[self.document.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Действует")
        # Из черновика документ не может сразу «утратить силу».
        self.assertNotContains(response, "Утратил силу")

    def test_controller_publishes_document(self):
        response = self._client(self.controller).post(
            reverse("documents:status", args=[self.document.pk]),
            {"new_status": NormativeDocument.Status.ACTIVE, "comment": "Приказ подписан"},
        )
        self.assertEqual(response.status_code, 302)
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, NormativeDocument.Status.ACTIVE)
        self.assertEqual(self.document.status_history.count(), 2)

    def test_disallowed_transition_posted_directly_is_rejected(self):
        # Форма такой вариант не предлагает — но её можно обойти, отправив
        # запрос напрямую, и сервис обязан проверить переход заново.
        response = self._client(self.controller).post(
            reverse("documents:status", args=[self.document.pk]),
            {"new_status": NormativeDocument.Status.REVOKED, "comment": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, NormativeDocument.Status.DRAFT)

    def test_archived_document_offers_no_transitions(self):
        archived = make_document(
            reg_number="403-п", status=NormativeDocument.Status.ARCHIVED,
            files_original="documents/originals/2026/01/scan.pdf",
        )
        response = self._client(self.controller).get(
            reverse("documents:status", args=[archived.pk])
        )
        self.assertContains(response, "конечное состояние")

    def test_restricted_document_write_pages_return_404(self):
        secret = make_document(
            reg_number="404-дсп",
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
            files_original="documents/originals/2026/01/scan.pdf",
        )
        client = self._client(self.controller)
        for name in ("documents:edit", "documents:status"):
            with self.subTest(page=name):
                self.assertEqual(client.get(reverse(name, args=[secret.pk])).status_code, 404)


class ActionButtonVisibilityTests(TestCase):
    def setUp(self):
        self.document = make_document(
            reg_number="410-п", files_original="documents/originals/2026/01/scan.pdf",
        )

    def _get(self, user, name, args=()):
        client = Client()
        client.login(personnel_number=user.personnel_number, password=PASSWORD)
        return client.get(reverse(name, args=args))

    def test_reader_sees_no_registration_button(self):
        response = self._get(make_user(personnel_number="0110"), "documents:list")
        self.assertNotContains(response, "Зарегистрировать документ")

    def test_methodist_sees_registration_button(self):
        methodist = make_user(personnel_number="0111", role=User.Role.METHODIST)
        response = self._get(methodist, "documents:list")
        self.assertContains(response, "Зарегистрировать документ")

    def test_controller_sees_status_button_on_card(self):
        controller = make_user(personnel_number="0112", role=User.Role.CONTROLLER_LAWYER)
        response = self._get(controller, "documents:detail", [self.document.pk])
        self.assertContains(response, "Изменить статус")


class DocumentCreationUploadTests(_WriteTestSetup, ClamdTestCase):
    """Успешная регистрация карточки — единственный тест записи, который
    реально доходит до хранилища.

    Отсюда живой clamd: `NormativeDocument.save()` сканирует загрузку до
    записи в WORM-бакет и работает fail-closed, поэтому без доступного
    антивируса создание карточки не проходит вовсе. Мок подменил бы
    ровно ту часть, которую и надо проверить — что штатный путь Web GUI
    проходит антивирусный контур целиком. Тот же принцип, что в
    test_antivirus_integration.py и test_ocr.py.
    """

    # Свой порт, чтобы не пересечься с другими ClamdTestCase при --parallel.
    clamd_port = 13312

    def test_methodist_creates_draft(self):
        scan = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        response = self._client(self.methodist).post(
            reverse("documents:create"), self._form_data(files_original=scan)
        )
        self.assertEqual(response.status_code, 302)
        created = NormativeDocument.objects.get(reg_number="401-п")
        self.assertEqual(created.status, NormativeDocument.Status.DRAFT)
        self.assertEqual(created.status_history.count(), 1)
        self.assertEqual(created.status_history.first().status, NormativeDocument.Status.DRAFT)
        created.files_original.delete(save=False)
