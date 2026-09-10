"""
Web GUI — вход и 2FA/TOTP (ТЗ 4.7). Серверный рендеринг (Django Templates
+ HTMX для enroll/confirm без полной перезагрузки страницы), сессия + CSRF.
Тонкий HTTP-слой поверх apps.iam.services — ни одна из этих вьюх не
обращается к User.objects/verify_totp_code напрямую, только к функциям
services.py (общим с apps/iam/api.py, см. их docstring).
"""
import datetime

from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import FormView

from apps.core.limits import consume_fixed_window, request_identity

from apps.audit.models import AuditLog

from . import services
from .security import mark_totp_verified
from .forms import LoginForm, PasswordChangeForm, TotpCodeForm
from .models import PASSWORD_EXPIRY_DAYS

_SESSION_PENDING_TICKET = "totp_pending_ticket"
_SESSION_SHARED_TERMINAL = "shared_terminal_login"
_SHARED_TERMINAL_SESSION_AGE = 15 * 60

# Application lockout in services.py (5 failed credentials / 15 min) remains
# the strict brute-force control. These larger per-minute limits protect HTTP
# resources from floods and intentionally do not duplicate account lockout.
_LOGIN_LIMIT = 60
_TOTP_LIMIT = 60
_WINDOW_SECONDS = 60


def _limit_auth_attempt(request, scope: str, *identity_parts: object) -> bool:
    result = consume_fixed_window(
        scope=scope,
        identity=request_identity(request, *identity_parts),
        limit=_LOGIN_LIMIT if scope == "iam.web.login" else _TOTP_LIMIT,
        window_seconds=_WINDOW_SECONDS,
    )
    return result.allowed


class LoginView(FormView):
    template_name = "iam/login.html"
    form_class = LoginForm

    def form_valid(self, form):
        if not _limit_auth_attempt(
            self.request, "iam.web.login", form.cleaned_data.get("personnel_number")
        ):
            form.add_error(None, "Слишком много попыток входа. Попробуйте позже.")
            return self.form_invalid(form)

        try:
            result = services.check_credentials(
                self.request,
                personnel_number=form.cleaned_data["personnel_number"],
                password=form.cleaned_data["password"],
            )
        except services.LoginBlocked:
            form.add_error(None, "Слишком много неудачных попыток входа. Попробуйте позже.")
            return self.form_invalid(form)
        if result is None:
            form.add_error(None, "Неверный табельный номер или пароль.")
            return self.form_invalid(form)

        shared_terminal = form.cleaned_data["shared_terminal"]
        if result.totp_required:
            self.request.session[_SESSION_PENDING_TICKET] = services.make_totp_pending_ticket(result.user)
            self.request.session[_SESSION_SHARED_TERMINAL] = shared_terminal
            return redirect("iam:login-verify-totp")

        django_login(self.request, result.user)
        self.request.session.set_expiry(_SHARED_TERMINAL_SESSION_AGE if shared_terminal else None)
        services.record_session_login(result.user, self.request)
        return redirect(self.get_success_url())

    def get_success_url(self):
        return settings.LOGIN_REDIRECT_URL


class TotpVerifyView(FormView):
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

        if not _limit_auth_attempt(self.request, "iam.web.totp", ticket):
            form.add_error(None, "Слишком много попыток подтверждения TOTP. Попробуйте позже.")
            return self.form_invalid(form)

        try:
            user = services.verify_totp_login(
                ticket=ticket,
                code=form.cleaned_data["code"],
                request=self.request,
            )
        except services.LoginBlocked:
            form.add_error(None, "Слишком много неудачных попыток входа. Попробуйте позже.")
            return self.form_invalid(form)
        if user is None:
            form.add_error(None, "Неверный код.")
            return self.form_invalid(form)

        shared_terminal = self.request.session.pop(_SESSION_SHARED_TERMINAL, False)
        del self.request.session[_SESSION_PENDING_TICKET]
        django_login(self.request, user)
        mark_totp_verified(self.request, user)
        self.request.session.set_expiry(_SHARED_TERMINAL_SESSION_AGE if shared_terminal else None)
        services.record_session_login(user, self.request)
        return redirect(settings.LOGIN_REDIRECT_URL)


class LogoutView(LoginRequiredMixin, View):
    def post(self, request):
        user = request.user
        django_logout(request)
        services.record_session_logout(user, request)
        return redirect("iam:login")


class TotpEnrollView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, "iam/totp_enroll.html", {"totp_enabled": request.user.totp_enabled})

    def post(self, request):
        if not _limit_auth_attempt(request, "iam.web.enroll", request.user.pk):
            from django.http import HttpResponse
            return HttpResponse("Слишком много попыток.", status=429)
        data = services.start_totp_enrollment(request.user)
        return render(request, "iam/_totp_enroll_result.html", {
            "secret": data["secret"],
            "provisioning_uri": data["provisioning_uri"],
            "form": TotpCodeForm(),
        })


class TotpConfirmView(LoginRequiredMixin, View):
    def post(self, request):
        if not _limit_auth_attempt(request, "iam.web.confirm", request.user.pk):
            from django.http import HttpResponse
            return HttpResponse("Слишком много попыток.", status=429)
        form = TotpCodeForm(request.POST)
        if not form.is_valid():
            return render(request, "iam/_totp_confirm_result.html", {"form": form}, status=400)

        try:
            enabled = services.confirm_totp_enrollment(request.user, code=form.cleaned_data["code"])
        except services.TotpEnrollmentNotStarted:
            return render(
                request,
                "iam/_totp_confirm_result.html",
                {"form": form, "not_started": True},
                status=400,
            )

        if not enabled:
            form.add_error(None, "Неверный код.")
            return render(request, "iam/_totp_confirm_result.html", {"form": form}, status=400)

        mark_totp_verified(request, request.user)
        return render(request, "iam/_totp_confirm_result.html", {"success": True})


class PasswordChangeView(LoginRequiredMixin, FormView):
    """Единственная вьюха смены пароля в проекте (по запросу ревью
    анти-фрода) — цель PasswordChangeRequiredMiddleware (apps/iam/middleware.py)
    для status=PASSWORD_CHANGE_REQUIRED/is_password_expired. Web-only —
    API остаётся информационным флагом (services.user_auth_summary),
    тем же разделением контуров, что и everywhere else в проекте (см.
    STACK.md о Web SSR vs External API JWT)."""

    template_name = "iam/password_change.html"
    form_class = PasswordChangeForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.save()
        # PasswordChangeForm меняет пароль — сессия иначе была бы
        # инвалидирована на СЛЕДУЮЩЕМ запросе (Django привязывает хэш
        # сессии к хэшу пароля), разлогинив только что сменившего пароль
        # пользователя на этой же странице.
        update_session_auth_hash(self.request, form.user)
        return redirect(settings.LOGIN_REDIRECT_URL)


class ProfileView(LoginRequiredMixin, View):
    """Личный кабинет (п.8.2 решения Заказчика).

    До этой партии единственным местом во всём Web GUI, где пользователь
    видел себя, была строка в шапке — имя и роль. Ни срока действия
    пароля, ни состояния 2FA, ни собственной истории входов ему было
    негде посмотреть, хотя всё это система про него знает и по всему
    этому его ограничивает.

    Страница только читает: смена пароля — на своей странице, 2FA — на
    своей, роль и допуск ДСП пользователь себе не назначает.
    """

    template_name = "iam/profile.html"
    RECENT_EVENTS = 10

    def get(self, request):
        user = request.user
        summary = services.user_auth_summary(user)

        # Собственные входы и выходы — из того же WORM-журнала, что
        # смотрит Офицер ИБ, но строго свои: фильтр по actor, а не по
        # табельному номеру из запроса.
        recent = (
            AuditLog.objects.filter(
                actor=user,
                event_type__in=[
                    AuditLog.EventType.SESSION_LOGIN,
                    AuditLog.EventType.SESSION_LOGOUT,
                    AuditLog.EventType.SESSION_LOGIN_FAILED,
                ],
            )
            .order_by("-created_at")[: self.RECENT_EVENTS]
        )

        return render(request, self.template_name, {
            "summary": summary,
            "recent_events": recent,
            "password_expires_at": _password_expiry(user),
        })


def _password_expiry(user):
    """Дата, после которой пароль потребует смены.

    Срок берётся из той же константы `PASSWORD_EXPIRY_DAYS`, по которой
    считает `User.is_password_expired` — иначе личный кабинет показывал
    бы одну дату, а middleware перенаправлял на смену пароля в другую.
    None, если пароль ещё ни разу не менялся: срока в этом случае нет,
    и придумывать его нельзя.
    """
    if not user.password_changed_at:
        return None
    return user.password_changed_at + datetime.timedelta(days=PASSWORD_EXPIRY_DAYS)
