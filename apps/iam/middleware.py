"""Enforce account policy on every session-based entry point, including admin."""
from django.shortcuts import redirect
from django.urls import reverse

from .security import session_totp_verified


class PasswordChangeRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        # API policy is evaluated after JWT authentication, in DRF permissions.
        if request.path.startswith("/api/"):
            return self.get_response(request)
        if request.path == reverse("admin:login"):
            return redirect(reverse("iam:login"))
        if user is not None and user.is_authenticated:
            allowed = {reverse("iam:login"), reverse("iam:logout"),
                       reverse("iam:login-verify-totp")}
            if request.path not in allowed:
                if user.totp_enabled and not session_totp_verified(request):
                    return redirect(reverse("iam:login"))
                if user.status == user.Status.PASSWORD_CHANGE_REQUIRED or user.is_password_expired:
                    if request.path != reverse("iam:password-change"):
                        return redirect(reverse("iam:password-change"))
                elif user.requires_totp and not user.totp_enabled:
                    if request.path not in {reverse("iam:totp-enroll"), reverse("iam:totp-confirm")}:
                        return redirect(reverse("iam:totp-enroll"))
        return self.get_response(request)
