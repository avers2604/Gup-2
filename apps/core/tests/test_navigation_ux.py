"""Ориентация в интерфейсе: активный раздел, крошки, skip-link.

Проверяется реальным рендером, а не чтением шаблонов. Django на опечатку
в имени переменной не ругается — он подставляет пустую строку, поэтому
крошка `{{ documnet.reg_number }}` выглядела бы в шаблоне правдоподобно и
молча рисовала пустой пункт.
"""
from django.test import TestCase
from django.urls import reverse

from apps.documents.models import NormativeDocument
from apps.documents.tests.factories import make_document
from apps.documents.tests.test_permissions import PASSWORD, login_client, make_user
from apps.iam.models import User


class NavActiveSectionTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0800", role=User.Role.METHODIST)
        self.client.login(personnel_number="0800", password=PASSWORD)

    def _nav(self, response):
        """Фрагмент разметки шапки — чтобы не ловить совпадения в теле страницы."""
        body = response.content.decode()
        start = body.index('<nav aria-label="Основная навигация">')
        return body[start:body.index("</nav>", start)]

    def test_current_section_is_marked(self):
        nav = self._nav(self.client.get(reverse("documents:list")))

        self.assertIn('href="/documents/" class="is-active" aria-current="page"', nav)

    def test_only_one_section_is_marked(self):
        """Три пункта живут под /documents/ — по префиксу зажглись бы все."""
        nav = self._nav(self.client.get(reverse("documents:list")))

        self.assertEqual(nav.count('aria-current="page"'), 1)

    def test_sibling_section_under_the_same_prefix_is_not_marked(self):
        nav = self._nav(self.client.get(reverse("documents:consolidated_list")))

        self.assertEqual(nav.count('aria-current="page"'), 1)
        marked = nav[: nav.index('aria-current="page"')]
        self.assertIn("/documents/consolidated/", marked.rsplit("<a ", 1)[-1])

    def test_nested_page_keeps_its_section_marked(self):
        """Карточка документа — всё ещё раздел «Документы»."""
        document = make_document(reg_number="NAV-1")

        nav = self._nav(self.client.get(reverse("documents:detail", args=[document.pk])))

        self.assertEqual(nav.count('aria-current="page"'), 1)

    def test_nothing_is_marked_in_a_section_without_a_menu_item(self):
        nav = self._nav(self.client.get(reverse("iam:profile")))

        self.assertEqual(nav.count('aria-current="page"'), 0)


class SkipLinkTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0810")
        self.client.login(personnel_number="0810", password=PASSWORD)

    def test_skip_link_points_at_an_existing_anchor(self):
        """Ссылка в никуда хуже её отсутствия: фокус просто не переедет."""
        body = self.client.get(reverse("documents:list")).content.decode()

        self.assertIn('href="#content"', body)
        self.assertIn('id="content"', body)

    def test_skip_link_precedes_navigation_in_tab_order(self):
        body = self.client.get(reverse("documents:list")).content.decode()

        self.assertLess(body.index('href="#content"'), body.index("<nav"))


class BreadcrumbTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0820", role=User.Role.METHODIST)
        self.client.login(personnel_number="0820", password=PASSWORD)
        self.document = make_document(
            reg_number="КР-1", status=NormativeDocument.Status.ACTIVE_AMENDED
        )

    def _crumbs(self, url):
        body = self.client.get(url).content.decode()
        self.assertIn('class="breadcrumbs"', body, f"на {url} нет крошек")
        start = body.index('class="breadcrumbs"')
        return body[start:body.index("</nav>", start)]

    def test_document_card_crumbs_name_the_document(self):
        crumbs = self._crumbs(reverse("documents:detail", args=[self.document.pk]))

        self.assertIn("Документы", crumbs)
        self.assertIn("КР-1", crumbs)
        self.assertIn('href="/documents/"', crumbs)

    def test_consolidated_crumbs_lead_back_to_their_own_section(self):
        """Сводка по документу возвращает в «Сводные редакции», не в реестр."""
        crumbs = self._crumbs(reverse("documents:consolidated_detail", args=[self.document.pk]))

        self.assertIn("Сводные редакции", crumbs)
        self.assertIn("/documents/consolidated/", crumbs)
        self.assertIn("КР-1", crumbs)

    def test_status_form_crumbs_have_three_levels(self):
        """Смена статуса разрешена только Администратору — им и смотрим."""
        admin = make_user(personnel_number="0821", role=User.Role.ADMINISTRATOR)
        self.client = login_client(admin)

        crumbs = self._crumbs(reverse("documents:status", args=[self.document.pk]))

        self.assertEqual(crumbs.count("<li"), 3)
        self.assertIn("Смена статуса", crumbs)

    def test_registration_form_crumb_says_registration_not_an_empty_number(self):
        """У новой карточки нет рег. номера — крошка не должна быть пустой."""
        crumbs = self._crumbs(reverse("documents:create"))

        self.assertIn("Регистрация", crumbs)

    def test_last_crumb_is_not_a_link(self):
        crumbs = self._crumbs(reverse("documents:detail", args=[self.document.pk]))
        last = crumbs.rsplit("<li", 1)[-1]

        self.assertIn('aria-current="page"', last)
        self.assertNotIn("<a ", last)
