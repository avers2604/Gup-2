from __future__ import annotations

from abc import ABC

from rest_framework.throttling import BaseThrottle

from .limits import consume_fixed_window, request_identity


class FixedWindowThrottle(BaseThrottle, ABC):
    scope_name = "default"
    limit = 60
    window_seconds = 60

    def identity(self, request, view) -> str:
        return request_identity(request, view.__class__.__name__)

    def allow_request(self, request, view) -> bool:
        result = consume_fixed_window(
            scope=self.scope_name,
            identity=self.identity(request, view),
            limit=self.limit,
            window_seconds=self.window_seconds,
        )
        self._last_result = result
        return result.allowed

    def wait(self):
        return self.window_seconds
