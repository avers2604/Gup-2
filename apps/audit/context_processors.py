"""Признак доступа к журналу аудита для навигации.

Пункт меню «Журнал аудита» показывается не всем, а решает это тот же
`permissions.can_view_audit_log`, что закрывает саму страницу — иначе
меню и вьюха разъехались бы, и пользователь видел бы ссылку, ведущую в
404. Контекст-процессор, а не расчёт в каждой вьюхе: шапка общая для
всех страниц проекта.
"""
from . import permissions


def audit_access(request):
    user = getattr(request, "user", None)
    return {"can_view_audit_log": permissions.can_view_audit_log(user) if user else False}
