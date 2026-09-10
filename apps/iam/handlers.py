from __future__ import annotations

from apps.core.domain_events import register

from .sessions import force_logout_user


@register("user.blocked")
def logout_blocked_user(event):
    user = event.payload["user"]
    force_logout_user(user.pk)
