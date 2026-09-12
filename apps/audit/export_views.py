"""Hardened WORM audit export endpoint.

The primary Web UI path is POST and therefore receives Django's normal CSRF
protection. A guarded GET path is kept temporarily for backwards-compatible
internal clients/tests, but cross-site browser fetches are rejected using Fetch
Metadata plus Origin/Referer validation.
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    StreamingHttpResponse,
)
from django.utils import timezone
from django.views import View

from . import permissions
from .forms import AuditFilterForm
from .models import AuditLog
from .views import _export_rows, filtered_entries

DEFAULT_EXPORT_MAX_ROWS = 50_000
DEFAULT_EXPORT_RATE_PER_MINUTE = 10


def _same_origin(request, value: str) -> bool:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return False
    return parsed.scheme == request.scheme and parsed.netloc == request.get_host()


def _legacy_get_is_cross_site(request) -> bool:
    fetch_site = request.headers.get("Sec-Fetch-Site", "").lower()
    if fetch_site == "cross-site":
        return True

    origin = request.headers.get("Origin")
    if origin and not _same_origin(request, origin):
        return True

    referer = request.headers.get("Referer")
    if referer and not _same_origin(request, referer):
        return True

    return False


def _rate_limit_allows(user) -> bool:
    limit = int(
        getattr(
            settings,
            "AUDIT_EXPORT_RATE_LIMIT_PER_MINUTE",
            DEFAULT_EXPORT_RATE_PER_MINUTE,
        )
    )
    if limit <= 0:
        return True

    minute = timezone.now().strftime("%Y%m%d%H%M")
    key = f"audit-export:{user.pk}:{minute}"
    if cache.add(key, 1, timeout=70):
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        # A backend may evict the key between add() and incr(). Treat that as
        # a fresh window instead of turning a protective control into a 500.
        cache.set(key, 1, timeout=70)
        count = 1
    return count <= limit


class AuditLogExportView(LoginRequiredMixin, View):
    """Export a bounded snapshot and record successful exports in WORM audit."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not permissions.can_view_audit_log(request.user):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        # Legacy compatibility only. Modern UI uses POST+CSRF. A cross-site
        # <img>, iframe or navigation must never be able to start an export or
        # manufacture AUDIT_LOG_EXPORTED entries using the victim's session.
        if _legacy_get_is_cross_site(request):
            return HttpResponseForbidden("Cross-site audit export is not allowed.\n")
        return self._export(request, request.GET)

    def post(self, request):
        return self._export(request, request.POST)

    def _export(self, request, data):
        if not _rate_limit_allows(request.user):
            return HttpResponse(
                "Слишком много запросов на выгрузку журнала.\n",
                status=429,
                content_type="text/plain; charset=utf-8",
            )

        form = AuditFilterForm(data)
        if not form.is_valid():
            return HttpResponseBadRequest("Некорректные фильтры выгрузки.\n")

        snapshot_at = timezone.now()
        queryset = filtered_entries(form).filter(created_at__lte=snapshot_at)

        max_rows = max(
            int(getattr(settings, "AUDIT_EXPORT_MAX_ROWS", DEFAULT_EXPORT_MAX_ROWS)),
            1,
        )
        # COUNT over a sliced subquery stops at max_rows + 1. We only need to
        # know whether the export is too large; a full-table count would itself
        # preserve the resource-amplification problem this guard is meant to fix.
        matched_entries = queryset[: max_rows + 1].count()
        if matched_entries > max_rows:
            return HttpResponse(
                f"Выгрузка превышает лимит {max_rows} строк. Уточните фильтры.\n",
                status=413,
                content_type="text/plain; charset=utf-8",
            )

        applied = {}
        for key, value in form.cleaned_data.items():
            if value in (None, "", []):
                continue
            applied[key] = value.isoformat() if hasattr(value, "isoformat") else str(value)

        AuditLog.objects.create(
            event_type=AuditLog.EventType.AUDIT_LOG_EXPORTED,
            actor=request.user,
            actor_personnel_number=getattr(request.user, "personnel_number", ""),
            object_type="AuditLog",
            object_id="export",
            details={
                "filters": applied,
                "matched_entries": matched_entries,
                "format": "csv",
                "snapshot_at": snapshot_at.isoformat(),
                "max_rows": max_rows,
            },
        )

        stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        response = StreamingHttpResponse(
            _export_rows(queryset),
            content_type="text/csv; charset=utf-8",
        )
        response["Content-Disposition"] = f'attachment; filename="audit-log-{stamp}.csv"'
        return response
