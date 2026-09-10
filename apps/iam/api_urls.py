from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import api

app_name = "iam_api"

urlpatterns = [
    path("token/", api.TokenObtainView.as_view(), name="token-obtain"),
    path("token/verify-totp/", api.TotpVerifyView.as_view(), name="token-verify-totp"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("me/", api.MeView.as_view(), name="me"),
]
