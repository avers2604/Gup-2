from django.test import TestCase

from apps.core.business_metrics import record_search
from apps.core.models import BusinessMetricCounter


class SearchMetricSemanticsTests(TestCase):
    def _value(self, name):
        row = BusinessMetricCounter.objects.filter(name=name).first()
        return row.value if row else 0

    def test_first_result_page_counts_as_one_logical_search(self):
        record_search(0, page_number=1)
        self.assertEqual(self._value("search_requests"), 1)
        self.assertEqual(self._value("search_zero_results"), 1)

    def test_pagination_does_not_count_as_new_search_or_new_zero_result(self):
        record_search(0, page_number=2)
        record_search(37, page_number=3)
        self.assertEqual(self._value("search_requests"), 0)
        self.assertEqual(self._value("search_zero_results"), 0)

    def test_invalid_page_number_is_rejected_instead_of_silently_polluting_metric(self):
        with self.assertRaises(ValueError):
            record_search(5, page_number=0)
        with self.assertRaises(ValueError):
            record_search(5, page_number=-1)

