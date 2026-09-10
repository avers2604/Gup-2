from __future__ import annotations

from apps.core.drf_throttles import FixedWindowThrottle
from apps.core.limits import client_ip


class SearchApiThrottle(FixedWindowThrottle):
    scope_name = "search.documents"
    limit = 30
    window_seconds = 60

    def identity(self, request, view) -> str:
        return client_ip(request)
