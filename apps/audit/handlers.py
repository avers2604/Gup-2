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
