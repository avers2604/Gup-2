from django.core.cache import cache
from django.test import TestCase

from apps.core.business_metrics import build_search_metric_key, record_search
from apps.core.models import BusinessMetricCounter


class SearchMetricSemanticsTests(TestCase):
    def setUp(self):
        cache.clear()

    def _value(self, name):
        row = BusinessMetricCounter.objects.filter(name=name).first()
        return row.value if row else 0

    def test_first_result_page_counts_as_one_logical_search(self):
        record_search(0, page_number=1, logical_search_key="search-a")
        self.assertEqual(self._value("search_requests"), 1)
        self.assertEqual(self._value("search_zero_results"), 1)

    def test_pagination_does_not_count_as_new_search_or_new_zero_result(self):
        record_search(0, page_number=2, logical_search_key="search-a")
        record_search(37, page_number=3, logical_search_key="search-a")
        self.assertEqual(self._value("search_requests"), 0)
        self.assertEqual(self._value("search_zero_results"), 0)

    def test_same_logical_search_in_short_window_counts_once(self):
        record_search(0, logical_search_key="same-user-query-filter")
        record_search(0, logical_search_key="same-user-query-filter")
        self.assertEqual(self._value("search_requests"), 1)
        self.assertEqual(self._value("search_zero_results"), 1)

    def test_different_logical_search_key_counts_separately(self):
        record_search(0, logical_search_key="search-a")
        record_search(5, logical_search_key="search-b")
        self.assertEqual(self._value("search_requests"), 2)
        self.assertEqual(self._value("search_zero_results"), 1)

    def test_search_key_normalizes_query_but_preserves_filters_and_surface(self):
        base = build_search_metric_key(
            user_id=17,
            query="  Контактная   СЕТЬ ",
            category="instruction",
            service="power",
            surface="web",
        )
        normalized = build_search_metric_key(
            user_id=17,
            query="контактная сеть",
            category="instruction",
            service="power",
            surface="web",
        )
        other_filter = build_search_metric_key(
            user_id=17,
            query="контактная сеть",
            category="order",
            service="power",
            surface="web",
        )
        api = build_search_metric_key(
            user_id=17,
            query="контактная сеть",
            category="instruction",
            service="power",
            surface="api",
        )
        self.assertEqual(base, normalized)
        self.assertNotEqual(base, other_filter)
        self.assertNotEqual(base, api)

    def test_invalid_page_number_is_rejected_instead_of_silently_polluting_metric(self):
        with self.assertRaises(ValueError):
            record_search(5, page_number=0, logical_search_key="x")
        with self.assertRaises(ValueError):
            record_search(5, page_number=-1, logical_search_key="x")

