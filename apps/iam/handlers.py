from __future__ import annotations

from django.db import models as dj_models
from django.utils import timezone

from apps.core.domain_events import publish, register

from .models import PasswordHistoryEntry, User
from .sessions import force_logout_user


def _save_without_side_effects(self, *args, **kwargs):
    previous = (
        type(self).objects.filter(pk=self.pk)
        .values_list("status", "role", "password", flat=False)
        .first()
    )
    was_blocked = previous is not None and previous[0] == self.Status.BLOCKED
    previous_role = previous[1] if previous is not None else None
    previous_password_hash = previous[2] if previous is not None else None
    is_new = previous is None
    role_changed = not is_new and previous_role != self.role
    password_changed = is_new or previous_password_hash != self.password

    self.is_active = self.status != self.Status.BLOCKED
    if password_changed:
        self.password_changed_at = timezone.now()
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"password_changed_at"}

    dj_models.Model.save(self, *args, **kwargs)

    if self.status == self.Status.BLOCKED and not was_blocked:
        publish("user.blocked", user=self)

    if role_changed:
        publish(
            "user.role.changed",
            user=self,
            actor=getattr(self, "_audit_actor", None),
            previous_role=previous_role,
            new_role=self.role,
        )

    if password_changed and not is_new and previous_password_hash:
        publish(
            "user.password.changed",
            user=self,
            previous_password_hash=previous_password_hash,
        )


User.save = _save_without_side_effects


@register("user.blocked")
def logout_blocked_user(event):
    user = event.payload["user"]
    force_logout_user(user.pk)


@register("user.password.changed")
def record_password_history(event):
    user = event.payload["user"]
    previous_password_hash = event.payload["previous_password_hash"]
    PasswordHistoryEntry.objects.create(user=user, password_hash=previous_password_hash)
    stale_ids = list(
        PasswordHistoryEntry.objects.filter(user=user)
        .order_by("-created_at")
        .values_list("id", flat=True)[User.PASSWORD_HISTORY_DEPTH :]
    )
    if stale_ids:
        PasswordHistoryEntry.objects.filter(id__in=stale_ids).delete()
