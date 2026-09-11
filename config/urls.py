from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.permissions import IsAuthenticated

from apps.iam.security import AccountReady


# drf-spectacular задаёт собственные permission_classes и тем самым не наследует
# DEFAULT_PERMISSION_CLASSES из REST_FRAMEWORK. Схема и Swagger относятся к тому
# же External API-контуру, поэтому обязаны соблюдать не только наличие JWT, но и
# account-policy (смена пароля / обязательная 2FA) через AccountReady.
API_DOCUMENTATION_PERMISSIONS = [IsAuthenticated, AccountReady]


urlpatterns = [
    path("admin/", admin.site.urls),
    # Web GUI (сессия/CSRF, Django Templates+HTMX) и External API (JWT,
    # DRF) — два независимых контура на разных префиксах, см. STACK.md.
    path("accounts/", include("apps.iam.urls")),
    # Рабочие места (ТЗ 4.1) — реестр и карточка НРД. До этого работа с
    # карточками была возможна только через /admin/.
    path("documents/", include("apps.documents.urls")),
    path("templates/", include("apps.templates_bank.urls")),
    path("audit/", include("apps.audit.urls")),
    path("api/v1/auth/", include("apps.iam.api_urls")),
    path("api/v1/search/", include("apps.search_ocr.api_urls")),
    path(
        "api/v1/schema/",
        SpectacularAPIView.as_view(permission_classes=API_DOCUMENTATION_PERMISSIONS),
        name="schema",
    ),
    path(
        "api/v1/docs/",
        SpectacularSwaggerView.as_view(
            url_name="schema",
            permission_classes=API_DOCUMENTATION_PERMISSIONS,
        ),
        name="api-docs",
    ),
    # Smart Search (ТЗ 4.4.1) — домашняя страница Web GUI, на неё уже
    # ссылалась шапка (templates/base.html) до появления самой страницы.
    path("", include("apps.search_ocr.urls")),
    path("", include("apps.core.urls")),
]
