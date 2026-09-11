from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from apps.core.models import BusinessMetricCounter
from apps.documents.models import NormativeDocument
from apps.documents.tests.factories import make_document
from apps.iam.models import Department, User


class SearchPaginationMetricIntegrationTests(TestCase):
    def setUp(self):
        cache.clear()
        dept, _ = Department.objects.get_or_create(
            name="Служба метрик поиска",
            defaults={"level": Department.Level.SERVICE},
        )
        self.user = User.objects.create(
            personnel_number="98111",
            last_name="Метриков",
            first_name="Пётр",
            position="Тестировщик",
            department=dept,
            role=User.Role.READER,
        )
        self.client = Client()
        self.client.force_login(self.user)
        for index in range(25):
            make_document(
                reg_number=f"PAGE-{index:02d}",
                title=f"Регламент контактной сети {index}",
                status=NormativeDocument.Status.ACTIVE,
            )

    def _value(self, name):
        row = BusinessMetricCounter.objects.filter(name=name).first()
        return row.value if row else 0

    def test_second_web_page_does_not_create_second_logical_search_event(self):
        url = reverse("search_ocr:search")
        first = self.client.get(url, {"q": "контактной сети"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.context["page_obj"].number, 1)
        self.assertEqual(self._value("search_requests"), 1)

        second = self.client.get(url, {"q": "контактной сети", "page": 2})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.context["page_obj"].number, 2)
        self.assertEqual(self._value("search_requests"), 1)
        self.assertEqual(self._value("search_zero_results"), 0)

    def test_immediate_repeat_of_same_first_page_counts_once(self):
        url = reverse("search_ocr:search")
        first = self.client.get(url, {"q": "контактной сети"})
        repeat = self.client.get(url, {"q": "  КОНТАКТНОЙ   СЕТИ  "})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(self._value("search_requests"), 1)

    def test_different_filter_is_a_distinct_logical_search(self):
        url = reverse("search_ocr:search")
        self.client.get(url, {"q": "контактной сети"})
        self.client.get(url, {"q": "контактной сети", "category": "order"})
        self.assertEqual(self._value("search_requests"), 2)
