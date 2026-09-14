from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.documents.models import NormativeDocument
from apps.iam.models import Department, User


@override_settings(WEB_SEARCH_RATE_LIMIT=2, WEB_SEARCH_RATE_WINDOW_SECONDS=60)
class WebSearchRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        department, _ = Department.objects.get_or_create(
            name="Служба лимитов поиска",
            defaults={"level": Department.Level.SERVICE},
        )
        self.user = User.objects.create(
            personnel_number="98991",
            last_name="Лимитов",
            first_name="Тест",
            position="Тестировщик",
            department=department,
            role=User.Role.READER,
        )
        self.client = Client()
        self.client.force_login(self.user)

    @patch("apps.search_ocr.views.search_documents_indexed")
    def test_third_search_is_rejected_before_expensive_search(self, search_mock):
        search_mock.return_value = NormativeDocument.objects.none()
        url = reverse("search_ocr:search")

        first = self.client.get(url, {"q": "контактная сеть"})
        second = self.client.get(url, {"q": "подстанция"})
        third = self.client.get(url, {"q": "трамвай"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(third.status_code, 429)
        self.assertEqual(third["Retry-After"], "60")
        self.assertEqual(search_mock.call_count, 2)

    def test_empty_search_page_does_not_consume_search_budget(self):
        url = reverse("search_ocr:search")
        for _ in range(5):
            self.assertEqual(self.client.get(url).status_code, 200)
