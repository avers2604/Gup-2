from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Web GUI (сессия/CSRF, Django Templates+HTMX) и External API (JWT,
    # DRF) — два независимых контура на разных префиксах, см. STACK.md.
    path("accounts/", include("apps.iam.urls")),
    path("api/v1/auth/", include("apps.iam.api_urls")),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/v1/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="api-docs"),
    path("", include("apps.core.urls")),
]
