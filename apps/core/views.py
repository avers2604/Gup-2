from django.db import DatabaseError, connection
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView


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


class StyleguideView(TemplateView):
    """Живой каталог компонентов — веб-порт правил из DESIGN.md (см. дополнение к ТЗ, п.3)."""

    template_name = "styleguide.html"
