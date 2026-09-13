"""Работа с реестром: сортировка, видимые фильтры, пустые состояния.

Реестр — экран, за которым методист сидит каждый день, и три вещи в нём
раньше не работали: порядок строк нельзя было задать, применённые фильтры
были видны только в адресной строке, а пустой результат выглядел
одинаково и когда фильтры ничего не нашли, и когда документов нет вовсе.
"""
import datetime

from django.test import Client, TestCase
from django.urls import reverse

from apps.core.sorting import Column, resolve
from apps.documents.models import NormativeDocument
from apps.iam.models import Department, User

from .factories import make_document
from .test_permissions import PASSWORD, make_user


class SortResolveTests(TestCase):
    """Разбор параметра сортировки — без БД и без HTTP."""

    columns = {"reg_number": Column("reg_number", "Рег. номер", ("reg_number",))}
    default = ("-reg_date", "reg_number")

    def test_known_column_sorts_ascending_by_default(self):
        key, descending, order = resolve(self.columns, "reg_number", self.default)

        self.assertEqual((key, descending, order), ("reg_number", False, ("reg_number",)))

    def test_minus_prefix_reverses(self):
        key, descending, order = resolve(self.columns, "-reg_number", self.default)

        self.assertEqual((key, descending, order), ("reg_number", True, ("-reg_number",)))

    def test_unknown_column_falls_back_to_the_default_order(self):
        """Параметр приходит из ссылки — испорченный не должен ронять реестр."""
        key, descending, order = resolve(self.columns, "issuer_dept__head__password", self.default)

        self.assertEqual(order, self.default)
        self.assertEqual(key, "", "ни один заголовок не должен подсветиться")

    def test_empty_and_missing_parameter_behave_the_same(self):
        self.assertEqual(resolve(self.columns, None, self.default), resolve(self.columns, "", self.default))


class RegistrySortingTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0900")
        self.client_ = Client()
        self.client_.login(personnel_number="0900", password=PASSWORD)

        other, _ = Department.objects.get_or_create(
            name="Служба пути", defaults={"level": Department.Level.SERVICE}
        )
        # Одинаковая дата регистрации у всех трёх: проверяем именно
        # запрошенный порядок, а не совпадение с порядком по умолчанию.
        self.b = make_document(reg_number="Б-2", title="Второй")
        self.a = make_document(reg_number="А-1", title="Первый", issuer_dept=other)
        self.c = make_document(reg_number="В-3", title="Третий")

    def _numbers(self, query=""):
        response = self.client_.get(reverse("documents:list") + query)
        return [doc.reg_number for doc in response.context["results"]]

    def test_sorting_by_a_column_reorders_rows(self):
        self.assertEqual(self._numbers("?sort=reg_number"), ["А-1", "Б-2", "В-3"])

    def test_descending_sort_reverses_the_order(self):
        self.assertEqual(self._numbers("?sort=-reg_number"), ["В-3", "Б-2", "А-1"])

    def test_unknown_sort_key_does_not_break_the_page(self):
        response = self.client_.get(reverse("documents:list") + "?sort=../../etc/passwd")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["results"]), 3)

    def test_sorting_by_related_field_uses_its_label_not_its_id(self):
        """Подразделение сортируется по названию, а не по UUID."""
        self.assertEqual(self._numbers("?sort=issuer_dept")[0], "Б-2")

    def test_ties_are_broken_by_reg_number(self):
        """Без устойчивого добивающего поля строки прыгают между страницами."""
        self.assertEqual(self._numbers("?sort=doc_type"), ["А-1", "Б-2", "В-3"])

    def test_header_link_of_the_active_column_flips_direction(self):
        response = self.client_.get(reverse("documents:list") + "?sort=reg_number")
        active = [column for column in response.context["columns"] if column["is_active"]]

        self.assertEqual(len(active), 1)
        self.assertIn("sort=-reg_number", active[0]["url"])
        self.assertEqual(active[0]["aria_sort"], "ascending")

    def test_sort_links_keep_the_filters(self):
        response = self.client_.get(reverse("documents:list") + "?doc_type=order&sort=reg_number")
        urls = [column["url"] for column in response.context["columns"]]

        self.assertTrue(all("doc_type=order" in url for url in urls))
        self.assertTrue(all(url.count("sort=") == 1 for url in urls), "sort не должен дублироваться")

    def test_no_column_is_active_under_the_default_order(self):
        """Этот порядок пользователь не выбирал — помечать нечего."""
        response = self.client_.get(reverse("documents:list"))

        self.assertEqual([c for c in response.context["columns"] if c["is_active"]], [])


class AppliedFiltersTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0910")
        self.client_ = Client()
        self.client_.login(personnel_number="0910", password=PASSWORD)
        self.document = make_document(reg_number="Ф-1")

    def test_applied_filters_are_shown_with_human_labels(self):
        """«order» в плашке не говорит пользователю ничего."""
        response = self.client_.get(reverse("documents:list") + "?doc_type=order")

        self.assertContains(response, "Применены фильтры")
        self.assertContains(response, "Приказ директора")

    def test_department_filter_is_shown_by_name(self):
        response = self.client_.get(
            reverse("documents:list") + f"?issuer_dept={self.document.issuer_dept.pk}"
        )

        self.assertContains(response, self.document.issuer_dept.name)

    def test_date_filter_is_shown_in_the_familiar_format(self):
        response = self.client_.get(reverse("documents:list") + "?effective_from=2026-01-01")

        self.assertContains(response, "01.01.2026")

    def test_nothing_is_shown_without_filters(self):
        response = self.client_.get(reverse("documents:list"))

        self.assertNotContains(response, "Применены фильтры")


class EmptyStateTests(TestCase):
    def setUp(self):
        self.reader = make_user(personnel_number="0920")
        self.methodist = make_user(personnel_number="0921", role=User.Role.METHODIST)

    def _client(self, user):
        client = Client()
        client.login(personnel_number=user.personnel_number, password=PASSWORD)
        return client

    def test_empty_registry_tells_the_editor_what_to_do(self):
        response = self._client(self.methodist).get(reverse("documents:list"))

        self.assertContains(response, "В реестре пока нет документов")
        self.assertContains(response, "Зарегистрируйте первую карточку")

    def test_empty_registry_does_not_offer_registration_to_a_reader(self):
        response = self._client(self.reader).get(reverse("documents:list"))

        self.assertContains(response, "В реестре пока нет документов")
        self.assertNotContains(response, "Зарегистрируйте первую карточку")

    def test_filtered_to_nothing_says_so_and_offers_a_reset(self):
        """Разные причины пустоты — разные сообщения, иначе оно бесполезно."""
        make_document(
            reg_number="Е-1", effective_date=datetime.date(2026, 1, 2),
            status=NormativeDocument.Status.ACTIVE,
        )

        response = self._client(self.reader).get(
            reverse("documents:list") + "?status=revoked"
        )

        self.assertContains(response, "Под фильтры не подошёл ни один документ")
        self.assertNotContains(response, "В реестре пока нет документов")
        self.assertContains(response, "Сбросить фильтры")
