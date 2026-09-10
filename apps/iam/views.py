"""
Web GUI — вход и 2FA/TOTP (ТЗ 4.7). Серверный рендеринг (Django Templates
+ HTMX для enroll/confirm без полной перезагрузки страницы), сессия + CSRF.
Тонкий HTTP-слой поверх apps.iam.services — ни одна из этих вьюх не
обращается к User.objects/verify_totp_code напрямую, только к функциям
services.py (общим с apps/iam/api.py, см. их docstring).
"""
from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import FormView

from . import services
from .forms import LoginForm, TotpCodeForm

_SESSION_PENDING_TICKET = "totp_pending_ticket"


class LoginView(FormView):
    template_name = "iam/login.html"
    form_class = LoginForm

    def form_valid(self, form):
        result = services.check_credentials(
            self.request,
            personnel_number=form.cleaned_data["personnel_number"],
            password=form.cleaned_data["password"],
        )
        if result is None:
            form.add_error(None, "Неверный табельный номер или пароль.")
            return self.form_invalid(form)

        if result.totp_required:
            self.request.session[_SESSION_PENDING_TICKET] = services.make_totp_pending_ticket(result.user)
            return redirect("iam:login-verify-totp")

        django_login(self.request, result.user)
        services.record_session_login(result.user)
        return redirect(self.get_success_url())

    def get_success_url(self):
        return settings.LOGIN_REDIRECT_URL


class TotpVerifyView(FormView):
    """Шаг 2 — только если в сессии есть тикет, оставленный LoginView.
    Сама сессия на этом этапе ещё НЕ авторизована (django_login() не
    вызывался) — тикет живёт в session ровно как переносчик состояния
    между двумя запросами одного браузера, не как признак входа."""

    template_name = "iam/totp_verify.html"
    form_class = TotpCodeForm

    def get(self, request, *args, **kwargs):
        if _SESSION_PENDING_TICKET not in request.session:
            return redirect("iam:login")
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        ticket = self.request.session.get(_SESSION_PENDING_TICKET)
        if not ticket:
            return redirect("iam:login")

        user = services.verify_totp_login(ticket=ticket, code=form.cleaned_data["code"])
        if user is None:
            form.add_error(None, "Неверный код.")
            return self.form_invalid(form)

        del self.request.session[_SESSION_PENDING_TICKET]
        django_login(self.request, user)
        services.record_session_login(user)
        return redirect(settings.LOGIN_REDIRECT_URL)


class LogoutView(LoginRequiredMixin, View):
    """POST-only (Django 5-конвенция — выход не должен срабатывать по
    голой GET-ссылке без подтверждения/CSRF)."""

    def post(self, request):
        user = request.user
        django_logout(request)
        services.record_session_logout(user)
        return redirect("iam:login")


class TotpEnrollView(LoginRequiredMixin, View):
    """GET — страница с кнопкой начала подключения 2FA (hx-post на этот
    же URL). POST — генерирует секрет и возвращает HTMX-фрагмент с
    провижининг-URI и формой подтверждения кода — без перезагрузки
    страницы. Прогрессивная деградация без JS не реализована (внутренний
    инструмент с контролируемым набором браузеров, не публичный сайт)."""

    def get(self, request):
        return render(request, "iam/totp_enroll.html", {"totp_enabled": request.user.totp_enabled})

    def post(self, request):
        data = services.start_totp_enrollment(request.user)
        return render(request, "iam/_totp_enroll_result.html", {
            "secret": data["secret"],
            "provisioning_uri": data["provisioning_uri"],
            "form": TotpCodeForm(),
        })


class TotpConfirmView(LoginRequiredMixin, View):
    def post(self, request):
        form = TotpCodeForm(request.POST)
        if not form.is_valid():
            return render(request, "iam/_totp_confirm_result.html", {"form": form}, status=400)

        try:
            enabled = services.confirm_totp_enrollment(request.user, code=form.cleaned_data["code"])
        except services.TotpEnrollmentNotStarted:
            return render(
                request, "iam/_totp_confirm_result.html",
                {"form": form, "not_started": True}, status=400,
            )

        if not enabled:
            form.add_error(None, "Неверный код.")
            return render(request, "iam/_totp_confirm_result.html", {"form": form}, status=400)

        return render(request, "iam/_totp_confirm_result.html", {"success": True})
