"""Обработчики доменных событий учётной записи (apps.core.domain_events).

Побочные эффекты, выходящие за границу самой записи User: принудительный
сброс сессий при блокировке и ведение истории паролей. Сами события
публикует User.save() (apps/iam/models.py) — этот модуль ничего не
подменяет и не патчит, только подписывается; подключается один раз из
IamConfig.ready().
"""
from __future__ import annotations

from apps.core.domain_events import register

from .models import PASSWORD_HISTORY_DEPTH, PasswordHistoryEntry
from .sessions import force_logout_user


@register("user.blocked")
def logout_blocked_user(event):
    force_logout_user(event.payload["user"].pk)


@register("user.password.changed")
def record_password_history(event):
    user = event.payload["user"]
    PasswordHistoryEntry.objects.create(
        user=user,
        password_hash=event.payload["previous_password_hash"],
    )
    stale_ids = list(
        PasswordHistoryEntry.objects.filter(user=user)
        .order_by("-created_at")
        .values_list("id", flat=True)[PASSWORD_HISTORY_DEPTH:]
    )
    if stale_ids:
        PasswordHistoryEntry.objects.filter(id__in=stale_ids).delete()
