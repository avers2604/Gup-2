from django.db import DatabaseError, connection
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from django.views.generic import TemplateView

from .business_metrics import render_prometheus


@require_safe
@never_cache
def health(request):
    """Minimal readiness endpoint for HA/load-balancer/acceptance checks.

    It deliberately exposes no infrastructure details. A successful response
    means Django can execute a trivial query through the configured database
    endpoint; database unavailability returns HTTP 503.
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return HttpResponse("unhealthy\n", status=503, content_type="text/plain")

    return HttpResponse("healthy\n", content_type="text/plain")


@require_safe
@never_cache
def business_metrics(request):
    """Prometheus text endpoint for Stage 4 business observability indicators."""
    return HttpResponse(
        render_prometheus(),
        content_type="text/plain; version=0.0.4; charset=utf-8",
    )


class StyleguideView(TemplateView):
    """Живой каталог компонентов — веб-порт правил из DESIGN.md (см. дополнение к ТЗ, п.3)."""

    template_name = "styleguide.html"
