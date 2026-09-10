from django.urls import path

from . import views

app_name = "iam"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("login/verify-totp/", views.TotpVerifyView.as_view(), name="login-verify-totp"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("totp/enroll/", views.TotpEnrollView.as_view(), name="totp-enroll"),
    path("totp/confirm/", views.TotpConfirmView.as_view(), name="totp-confirm"),
]
