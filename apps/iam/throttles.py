from __future__ import annotations

from apps.core.drf_throttles import FixedWindowThrottle
from apps.core.limits import client_ip, request_identity


class TokenObtainThrottle(FixedWindowThrottle):
    scope_name = "iam.token_obtain"
    # Credential lockout remains the strict brute-force control (5 failures
    # / 15 minutes). This HTTP cap protects the endpoint from request floods
    # without accidentally becoming the primary account lockout mechanism.
    limit = 60
    window_seconds = 60

    def identity(self, request, view) -> str:
        personnel_number = request.data.get("personnel_number", "") if hasattr(request, "data") else ""
        return request_identity(request, view.__class__.__name__, personnel_number)


class TotpVerifyThrottle(FixedWindowThrottle):
    scope_name = "iam.totp_verify"
    limit = 60
    window_seconds = 60

    def identity(self, request, view) -> str:
        return request_identity(request, view.__class__.__name__)


class TokenRefreshThrottle(FixedWindowThrottle):
    scope_name = "iam.token_refresh"
    limit = 120
    window_seconds = 60

    def identity(self, request, view) -> str:
        return client_ip(request)


class MeThrottle(FixedWindowThrottle):
    scope_name = "iam.me"
    limit = 300
    window_seconds = 60
