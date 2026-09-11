from __future__ import annotations

from apps.core.domain_events import register

from .models import AuditLog


@register("user.role.changed")
def audit_user_role_changed(event):
    payload = event.payload
    actor = payload.get("actor")
    user = payload["user"]
    AuditLog.objects.create(
        event_type=AuditLog.EventType.USER_ROLE_CHANGED,
        actor=actor,
        actor_personnel_number=getattr(actor, "personnel_number", ""),
        object_type="User",
        object_id=str(user.pk),
        details={
            "target_personnel_number": user.personnel_number,
            "previous_role": payload.get("previous_role"),
            "new_role": payload.get("new_role"),
        },
    )


@register("user.role.elevated")
def audit_user_role_elevated(event):
    payload = event.payload
    actor = payload.get("actor")
    user = payload["user"]
    AuditLog.objects.create(
        event_type=AuditLog.EventType.USER_ROLE_ELEVATED,
        actor=actor,
        actor_personnel_number=getattr(actor, "personnel_number", ""),
        object_type="User",
        object_id=str(user.pk),
        details={
            "target_personnel_number": user.personnel_number,
            "previous_role": payload.get("previous_role"),
            "new_role": payload.get("new_role"),
            "source": payload.get("source", "personnel_import"),
        },
    )


@register("auth.login.failed")
def audit_login_failed(event):
    payload = event.payload
    actor = payload.get("actor")
    AuditLog.objects.create(
        event_type=AuditLog.EventType.SESSION_LOGIN_FAILED,
        actor=actor,
        actor_personnel_number=payload.get("personnel_number", ""),
        object_type="User",
        object_id=payload.get("object_id", ""),
        details={
            "ip_address": payload.get("ip_address", ""),
            "stage": payload.get("stage", ""),
            "reason": payload.get("reason", ""),
        },
    )


@register("auth.session.login")
def audit_session_login(event):
    payload = event.payload
    user = payload["user"]
    AuditLog.objects.create(
        event_type=AuditLog.EventType.SESSION_LOGIN,
        actor=user,
        actor_personnel_number=user.personnel_number,
        object_type="User",
        object_id=str(user.pk),
        details={
            "full_name": user.full_name,
            "role": user.role,
            "ip_address": payload.get("ip_address", ""),
        },
    )


@register("auth.session.logout")
def audit_session_logout(event):
    payload = event.payload
    user = payload["user"]
    AuditLog.objects.create(
        event_type=AuditLog.EventType.SESSION_LOGOUT,
        actor=user,
        actor_personnel_number=user.personnel_number,
        object_type="User",
        object_id=str(user.pk),
        details={
            "full_name": user.full_name,
            "role": user.role,
            "ip_address": payload.get("ip_address", ""),
        },
    )


@register("auth.totp.reset")
def audit_totp_reset(event):
    payload = event.payload
    actor = payload["actor"]
    user = payload["user"]
    AuditLog.objects.create(
        event_type=AuditLog.EventType.USER_TOTP_RESET,
        actor=actor,
        actor_personnel_number=actor.personnel_number,
        object_type="User",
        object_id=str(user.pk),
        details={"target_personnel_number": user.personnel_number},
    )
