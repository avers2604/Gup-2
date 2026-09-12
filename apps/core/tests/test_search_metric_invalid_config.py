from django.test import TestCase, override_settings

from apps.core.business_metrics import record_search
from apps.core.models import BusinessMetricCounter


class SearchMetricInvalidConfigTests(TestCase):
    @override_settings(SEARCH_METRIC_DEDUP_SECONDS=0)
    def test_nonpositive_dedupe_window_does_not_break_search(self):
        record_search(3, logical_search_key="invalid-config")

        counter = BusinessMetricCounter.objects.get(name="search_requests")
        self.assertEqual(counter.value, 1)
