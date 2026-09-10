"""Authentication, lockout and TOTP application service."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth import authenticate
from django.core import signing
from django.utils import timezone

from apps.audit.models import AuditLog

from .models import User
from .totp import generate_totp_secret, totp_provisioning_uri, verify_totp_code

_TOTP_PENDING_TICKET_SALT = "apps.iam.services.totp_pending_ticket"
_TOTP_PENDING_TICKET_MAX_AGE = 5 * 60
LOCKOUT_MAX_ATTEMPTS = 5
LOCKOUT_WINDOW = timedelta(minutes=15)


class TotpEnrollmentNotStarted(Exception):
    pass


class LoginBlocked(Exception):
    pass


@dataclass
class CredentialCheckResult:
    user: User
    totp_required: bool


def _client_ip(request) -> str:
    if request is None:
        return ""
    return request.META.get("REMOTE_ADDR", "") or ""


def _recent_failed_attempts(personnel_number: str) -> int:
    if not personnel_number:
        return 0
    return AuditLog.objects.filter(
        event_type=AuditLog.EventType.SESSION_LOGIN_FAILED,
        actor_personnel_number=personnel_number,
        created_at__gte=timezone.now() - LOCKOUT_WINDOW,
    ).count()


def is_locked_out(personnel_number: str) -> bool:
    return _recent_failed_attempts(personnel_number) >= LOCKOUT_MAX_ATTEMPTS


def check_credentials(request, *, personnel_number: str, password: str) -> CredentialCheckResult | None:
    if is_locked_out(personnel_number):
        raise LoginBlocked
    user = authenticate(request, username=personnel_number, password=password)
    if user is None:
        AuditLog.objects.create(
            event_type=AuditLog.EventType.SESSION_LOGIN_FAILED,
            actor=None,
            actor_personnel_number=personnel_number,
            object_type="User",
            object_id="",
            details={"ip_address": _client_ip(request), "stage": "credentials"},
        )
        return None
    return CredentialCheckResult(user=user, totp_required=user.totp_enabled)


def make_totp_pending_ticket(user: User) -> str:
    return signing.dumps({"user_id": str(user.pk)}, salt=_TOTP_PENDING_TICKET_SALT)


def verify_totp_login(*, ticket: str, code: str, request=None) -> User | None:
    try:
        data = signing.loads(
            ticket,
            salt=_TOTP_PENDING_TICKET_SALT,
            max_age=_TOTP_PENDING_TICKET_MAX_AGE,
        )
    except signing.BadSignature:
        AuditLog.objects.create(
            event_type=AuditLog.EventType.SESSION_LOGIN_FAILED,
            actor=None,
            actor_personnel_number="",
            object_type="User",
            object_id="",
            details={
                "ip_address": _client_ip(request),
                "stage": "totp",
                "reason": "invalid_or_expired_ticket",
            },
        )
        return None

    user = User.objects.filter(pk=data.get("user_id")).first()
    if user is None or not user.is_active:
        AuditLog.objects.create(
            event_type=AuditLog.EventType.SESSION_LOGIN_FAILED,
            actor=user,
            actor_personnel_number=user.personnel_number if user else "",
            object_type="User",
            object_id=str(user.pk) if user else "",
            details={
                "ip_address": _client_ip(request),
                "stage": "totp",
                "reason": "user_inactive_or_missing",
            },
        )
        return None

    if is_locked_out(user.personnel_number):
        raise LoginBlocked
    if not verify_totp_code(secret=user.totp_secret, code=code):
        AuditLog.objects.create(
            event_type=AuditLog.EventType.SESSION_LOGIN_FAILED,
            actor=user,
            actor_personnel_number=user.personnel_number,
            object_type="User",
            object_id=str(user.pk),
            details={
                "ip_address": _client_ip(request),
                "stage": "totp",
                "reason": "wrong_code",
            },
        )
        return None
    return user


def user_auth_summary(user: User) -> dict:
    return {
        "personnel_number": user.personnel_number,
        "full_name": user.full_name,
        "role": user.role,
        "totp_enabled": user.totp_enabled,
        "must_enroll_totp": user.requires_totp and not user.totp_enabled,
        "password_change_required": user.status == User.Status.PASSWORD_CHANGE_REQUIRED,
    }


def record_session_login(user: User, request=None) -> None:
    AuditLog.objects.create(
        event_type=AuditLog.EventType.SESSION_LOGIN,
        actor=user,
        actor_personnel_number=user.personnel_number,
        object_type="User",
        object_id=str(user.pk),
        details={
            "full_name": user.full_name,
            "role": user.role,
            "ip_address": _client_ip(request),
        },
    )


def record_session_logout(user: User, request=None) -> None:
    AuditLog.objects.create(
        event_type=AuditLog.EventType.SESSION_LOGOUT,
        actor=user,
        actor_personnel_number=user.personnel_number,
        object_type="User",
        object_id=str(user.pk),
        details={
            "full_name": user.full_name,
            "role": user.role,
            "ip_address": _client_ip(request),
        },
    )


def start_totp_enrollment(user: User) -> dict:
    secret = generate_totp_secret()
    user.totp_secret = secret
    user.save(update_fields=["totp_secret"])
    return {
        "secret": secret,
        "provisioning_uri": totp_provisioning_uri(
            secret=secret,
            personnel_number=user.personnel_number,
        ),
    }


def confirm_totp_enrollment(user: User, *, code: str) -> bool:
    if not user.totp_secret:
        raise TotpEnrollmentNotStarted
    if not verify_totp_code(secret=user.totp_secret, code=code):
        return False
    user.totp_enabled = True
    user.save(update_fields=["totp_enabled"])
    return True
