from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from django.core.cache import cache


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    count: int


def _cache_key(scope: str, identity: str, window_seconds: int) -> str:
    digest = sha256(identity.encode("utf-8")).hexdigest()
    return f"rate-limit:{scope}:{window_seconds}:{digest}"


def consume_fixed_window(scope: str, identity: str, limit: int, window_seconds: int) -> RateLimitResult:
    """Consume one token from a fixed-rate window.

    The cache backend is intentionally the only dependency: Django's default
    cache is sufficient for development, while a shared cache can be plugged
    in later without changing callers.
    """
    key = _cache_key(scope, identity, window_seconds)
    if cache.add(key, 1, timeout=window_seconds):
        return RateLimitResult(allowed=True, count=1)

    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window_seconds)
        count = 1

    return RateLimitResult(allowed=count <= limit, count=count)


def client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def request_identity(request, *parts: object) -> str:
    base = [client_ip(request), *[str(part) for part in parts if part not in (None, "")]]
    return "::".join(base)
