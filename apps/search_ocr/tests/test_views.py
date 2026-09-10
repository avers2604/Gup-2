"""Web GUI — apps/search_ocr/views.py (SearchView, ТЗ 4.4.1)."""
from django.test import Client, TestCase
from django.urls import reverse

from apps.documents.models import NormativeDocument
from apps.documents.tests.factories import make_document
from apps.iam.models import Department, User


def _make_user(personnel_number="0001", **kwargs):
    dept, _ = Department.objects.get_or_create(
        name="Служба движения", defaults={"level": Department.Level.SERVICE}
    )
    defaults = dict(
        personnel_number=personnel_number, last_name="Иванов", first_name="Пётр",
        position="Водитель", department=dept, role=User.Role.READER,
    )
    defaults.update(kwargs)
    user = User(**defaults)
    user.set_password("Sup3r$ecret!Pass1234")
    user.save()
    return user


class SearchViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = _make_user()

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse("search_ocr:search"))
        self.assertRedirects(
            response, f"{reverse('iam:login')}?next={reverse('search_ocr:search')}"
        )

    def test_authenticated_get_without_query_renders_empty_form(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("search_ocr:search"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "search_ocr/search.html")
        self.assertIsNone(response.context["results"])

    def test_search_with_query_shows_results(self):
        make_document(
            reg_number="1-п", title="Приказ о тяговых подстанциях", status=NormativeDocument.Status.ACTIVE,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("search_ocr:search"), {"q": "тяговых подстанциях"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1-п")

    def test_search_no_results_shows_message(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("search_ocr:search"), {"q": "нет такого документа вообще"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ничего не найдено")

    def test_dsp_document_not_leaked_to_reader_without_access(self):
        make_document(
            reg_number="1-дсп", title="Секретный приказ о тяговых подстанциях",
            status=NormativeDocument.Status.ACTIVE, access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("search_ocr:search"), {"q": "тяговых подстанциях"})
        self.assertNotContains(response, "1-дсп")
