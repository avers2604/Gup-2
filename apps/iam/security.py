"""Shared authentication policy for browser sessions and JWT clients."""
from hashlib import sha256

from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.serializers import TokenRefreshSerializer

from .models import User


def totp_stamp(user):
    return sha256(user.totp_secret_encrypted.encode()).hexdigest()


def mark_totp_verified(request, user):
    request.session["totp_verified"] = totp_stamp(user)


def session_totp_verified(request):
    return request.session.get("totp_verified") == totp_stamp(request.user)


class AccountReady(BasePermission):
    message = "Завершите смену пароля и настройку 2FA через Web-интерфейс."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.status == User.Status.PASSWORD_CHANGE_REQUIRED or user.is_password_expired:
            return False
        if user.requires_totp and not user.totp_enabled:
            return False
        if user.totp_enabled:
            return request.auth is not None and request.auth.get("totp_stamp") == totp_stamp(user)
        return True


class PolicyJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if validated_token.get("auth_version", 0) != user.auth_version:
            raise AuthenticationFailed("Учётные данные изменены. Войдите повторно.")
        return user


class PolicyTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        token = self.token_class(attrs["refresh"])
        user = User.objects.filter(pk=token.get("user_id"), is_active=True).first()
        if (user is None or token.get("auth_version", 0) != user.auth_version
                or user.status == User.Status.PASSWORD_CHANGE_REQUIRED or user.is_password_expired
                or (user.requires_totp and not user.totp_enabled)
                or (user.totp_enabled and token.get("totp_stamp") != totp_stamp(user))):
            raise AuthenticationFailed("Требуется повторный вход и завершение настройки аккаунта.")
        return super().validate(attrs)
