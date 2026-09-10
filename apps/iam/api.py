"""
External API — вход по JWT (тот же двухшаговый 2FA-флоу, что и Web GUI,
см. apps/iam/services.py). Тонкий HTTP-слой контура интеграций (решение
Заказчика): под /api/v1/auth/, задокументирован drf-spectacular
(/api/v1/schema/, /api/v1/docs/ — config/urls.py). Ни эта вьюха, ни
apps/iam/views.py не обращаются к User.objects/verify_totp_code
напрямую — только через apps.iam.services.
"""
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from . import services
from .serializers import TokenObtainRequestSerializer, TotpVerifyRequestSerializer


def _issue_tokens(user) -> dict:
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        **services.user_auth_summary(user),
    }


class TokenObtainView(APIView):
    """Шаг 1. Пара JWT сразу — если у пользователя не включена 2FA.
    Иначе {"totp_required": true, "ticket": "..."} для шага 2
    (TotpVerifyView) — тот же подписанный тикет (services.py), что и в
    Web-контуре, не отдельный механизм."""

    permission_classes = [AllowAny]

    @extend_schema(
        request=TokenObtainRequestSerializer,
        responses={200: OpenApiResponse(description="Пара JWT либо запрос второго фактора (totp_required)")},
    )
    def post(self, request):
        serializer = TokenObtainRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = services.check_credentials(
            request,
            personnel_number=serializer.validated_data["personnel_number"],
            password=serializer.validated_data["password"],
        )
        if result is None:
            return Response(
                {"detail": "Неверный табельный номер или пароль."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if result.totp_required:
            ticket = services.make_totp_pending_ticket(result.user)
            return Response({"totp_required": True, "ticket": ticket})

        # SESSION_LOGIN здесь же, что и в Web-контуре — событие означает
        # "пользователь вошёл", не буквально "создана Django-сессия"; для
        # JWT это выдача пары токенов.
        services.record_session_login(result.user, request)
        return Response({"totp_required": False, **_issue_tokens(result.user)})


class TotpVerifyView(APIView):
    """Шаг 2 — тикет с шага 1 + код TOTP, возвращает пару JWT."""

    permission_classes = [AllowAny]

    @extend_schema(
        request=TotpVerifyRequestSerializer,
        responses={200: OpenApiResponse(description="Пара JWT")},
    )
    def post(self, request):
        serializer = TotpVerifyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = services.verify_totp_login(
            ticket=serializer.validated_data["ticket"],
            code=serializer.validated_data["code"],
            request=request,
        )
        if user is None:
            return Response({"detail": "Неверный код."}, status=status.HTTP_401_UNAUTHORIZED)

        services.record_session_login(user, request)
        return Response(_issue_tokens(user))


class MeView(APIView):
    """Кому принадлежит текущий access-токен — минимальный, но
    обязательный элемент любого JWT API: без него нечем проверить в
    тестах (и клиенту интеграции — в реальности), что Bearer-токен вообще
    даёт доступ к защищённым эндпоинтам, а не только выдаётся."""

    def get(self, request):
        return Response(services.user_auth_summary(request.user))
