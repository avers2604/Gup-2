"""По запросу ревью анти-фрода: превращает status=PASSWORD_CHANGE_REQUIRED
и User.is_password_expired из чисто информационных флагов (какими они
были честно задокументированы в STACK.md) в реальное ограничение —
единственная вьюха смены пароля в проекте появилась в этой же партии
(apps/iam/views.PasswordChangeView). Web-only (session-контур) —
External API/JWT сознательно не тронут: там это по-прежнему только флаг
в user_auth_summary (services.py), то же разделение контуров, что и
everywhere else в проекте (см. STACK.md про Web SSR vs External API JWT)."""
from django.shortcuts import redirect
from django.urls import reverse

# /accounts/ целиком (не только сам password/change/) — иначе цикл
# редиректов на login/logout/2FA-эндпоинтах для ещё не подтвердившего
# смену пароля пользователя. /admin/ и /api/ — вне области действия этой
# партии (см. её docstring выше про Web-only).
_EXEMPT_PREFIXES = ("/accounts/", "/admin/", "/api/")


class PasswordChangeRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None and user.is_authenticated
            and not request.path.startswith(_EXEMPT_PREFIXES)
            and (user.status == user.Status.PASSWORD_CHANGE_REQUIRED or user.is_password_expired)
        ):
            return redirect(reverse("iam:password-change"))
        return self.get_response(request)
