from rest_framework import serializers


class LoginSerializer(serializers.Serializer):
    personnel_number = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)


class TotpCodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=10)
