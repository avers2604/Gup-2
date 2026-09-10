"""Сериализаторы External API (apps/iam/api.py) — только валидация
HTTP-ввода, вся бизнес-логика в apps/iam/services.py."""
from rest_framework import serializers


class TokenObtainRequestSerializer(serializers.Serializer):
    personnel_number = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False, style={"input_type": "password"})


class TotpVerifyRequestSerializer(serializers.Serializer):
    ticket = serializers.CharField(help_text="Тикет, полученный на шаге token/ при totp_required=true")
    code = serializers.CharField(max_length=10)


class LogoutRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField(help_text="Refresh-токен, который нужно отозвать (blacklist)")


class UserSummarySerializer(serializers.Serializer):
    personnel_number = serializers.CharField()
    full_name = serializers.CharField()
    role = serializers.CharField()
    totp_enabled = serializers.BooleanField()
    must_enroll_totp = serializers.BooleanField()
    password_change_required = serializers.BooleanField()
