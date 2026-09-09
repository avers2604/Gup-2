"""
Логин с обязательным TOTP для ролей из `User.requires_totp` (ТЗ 4.7).
Двухшаговый флоу: LoginView проверяет табельный номер/пароль и, если у
пользователя включена 2FA, НЕ вызывает django_login() — сессия отмечается
как "ожидает код" (через request.session, не отдельное хранилище — сама
сессия уже round-trip'ится по cookie), полноценный вход происходит только
в LoginVerifyTotpView. Так исключается окно между "пароль верный" и
"второй фактор пройден", в котором уже есть валидная авторизованная сессия.
"""
from django.contrib.auth import authenticate
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditLog

from .models import User
from .serializers import LoginSerializer, TotpCodeSerializer
from .totp import generate_totp_secret, totp_provisioning_uri, verify_totp_code

_SESSION_PENDING_TOTP_USER_ID = "totp_pending_user_id"


def _user_summary(user: User) -> dict:
    return {
        "personnel_number": user.personnel_number,
        "full_name": user.full_name,
        "role": user.role,
        "totp_enabled": user.totp_enabled,
        # Роль требует 2FA (ТЗ 4.7), но пользователь ещё не прошёл enroll —
        # клиент должен направить его на /auth/totp/enroll/ следующим шагом.
        # Это сигнал, не блокировка: сам вход уже состоялся.
        "must_enroll_totp": user.requires_totp and not user.totp_enabled,
        # Информационный флаг — принудительная смена пароля до доступа к
        # остальному API не реализована на уровне permissions (см. STACK.md,
        # честная граница); клиент решает, что с этим делать.
        "password_change_required": user.status == User.Status.PASSWORD_CHANGE_REQUIRED,
    }


def _log_session_event(user: User, event_type: str) -> None:
    AuditLog.objects.create(
        event_type=event_type,
        actor=user,
        actor_personnel_number=user.personnel_number,
        object_type="User",
        object_id=str(user.pk),
    )


class LoginView(APIView):
    """Шаг 1: табельный номер + пароль."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = authenticate(
            request,
            username=serializer.validated_data["personnel_number"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            # Один и тот же ответ для "нет такого табельного номера",
            # "неверный пароль" и "пользователь заблокирован" (is_active
            # уже отсекается authenticate() через ModelBackend) — не
            # раскрываем оператору, какая именно часть неверна.
            return Response(
                {"detail": "Неверный табельный номер или пароль."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if user.totp_enabled:
            request.session[_SESSION_PENDING_TOTP_USER_ID] = str(user.pk)
            return Response({"totp_required": True})

        django_login(request, user)
        _log_session_event(user, AuditLog.EventType.SESSION_LOGIN)
        return Response({"totp_required": False, **_user_summary(user)})


class LoginVerifyTotpView(APIView):
    """Шаг 2: код TOTP, завершает вход, начатый LoginView."""

    permission_classes = [AllowAny]

    def post(self, request):
        pending_id = request.session.get(_SESSION_PENDING_TOTP_USER_ID)
        if not pending_id:
            return Response(
                {"detail": "Нет ожидающего подтверждения входа — начните с /auth/login/."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = TotpCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Перечитываем пользователя, а не полагаемся на снимок из шага 1:
        # между шагами (сколько бы времени ни прошло) его могли
        # заблокировать — is_active проверяется здесь так же, как
        # authenticate() уже проверял его на шаге 1.
        user = User.objects.filter(pk=pending_id).first()
        if (
            user is None
            or not user.is_active
            or not verify_totp_code(secret=user.totp_secret, code=serializer.validated_data["code"])
        ):
            return Response({"detail": "Неверный код."}, status=status.HTTP_401_UNAUTHORIZED)

        del request.session[_SESSION_PENDING_TOTP_USER_ID]
        django_login(request, user)
        _log_session_event(user, AuditLog.EventType.SESSION_LOGIN)
        return Response(_user_summary(user))


class LogoutView(APIView):
    def post(self, request):
        user = request.user
        django_logout(request)
        _log_session_event(user, AuditLog.EventType.SESSION_LOGOUT)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TotpEnrollView(APIView):
    """Начало включения 2FA — генерирует секрет, но НЕ включает
    totp_enabled: включение требует подтверждения кодом (TotpConfirmView),
    иначе пользователь рискует остаться без доступа, так и не убедившись,
    что приложение-аутентификатор реально синхронизировано с сервером."""

    def post(self, request):
        user = request.user
        secret = generate_totp_secret()
        user.totp_secret = secret
        user.save(update_fields=["totp_secret"])
        return Response({
            "secret": secret,
            "provisioning_uri": totp_provisioning_uri(
                secret=secret, personnel_number=user.personnel_number,
            ),
        })


class TotpConfirmView(APIView):
    def post(self, request):
        user = request.user
        serializer = TotpCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if not user.totp_secret:
            return Response(
                {"detail": "2FA не начата — сначала вызовите /auth/totp/enroll/."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not verify_totp_code(secret=user.totp_secret, code=serializer.validated_data["code"]):
            return Response({"detail": "Неверный код."}, status=status.HTTP_401_UNAUTHORIZED)

        user.totp_enabled = True
        user.save(update_fields=["totp_enabled"])
        return Response({"totp_enabled": True})
