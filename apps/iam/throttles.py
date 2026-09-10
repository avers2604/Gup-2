from __future__ import annotations

from apps.core.drf_throttles import FixedWindowThrottle
from apps.core.limits import client_ip, request_identity


class TokenObtainThrottle(FixedWindowThrottle):
    scope_name = "iam.token_obtain"
    limit = 5
    window_seconds = 60

    def identity(self, request, view) -> str:
        return request_identity(request, view.__class__.__name__)


class TotpVerifyThrottle(FixedWindowThrottle):
    scope_name = "iam.totp_verify"
    limit = 10
    window_seconds = 60

    def identity(self, request, view) -> str:
        return request_identity(request, view.__class__.__name__)


class TokenRefreshThrottle(FixedWindowThrottle):
    scope_name = "iam.token_refresh"
    limit = 30
    window_seconds = 60

    def identity(self, request, view) -> str:
        return client_ip(request)


class MeThrottle(FixedWindowThrottle):
    scope_name = "iam.me"
    limit = 120
    window_seconds = 60
