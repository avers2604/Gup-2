"""Права доступа к банку бланков (ТЗ 4.3).

Тот же приём, что в `apps/documents/permissions.py`: правила жили внутри
`TemplateAdmin._can_manage`, а с появлением рабочего места понадобились
второму потребителю. Вынесены сюда, admin вызывает эти же функции.

ОТКУДА ВЗЯТА МАТРИЦА. Круг управляющих ролей — дословно из прежнего
`TemplateAdmin._can_manage` (Контролёр/Юрист + Администратор; роль
«Куратор службы» упразднена, её функционал унаследовал Контролёр/Юрист —
решение Заказчика). Право просмотра ТЗ отдельно не ограничивает: бланк —
рабочий инструмент линейного персонала, ради которого банк форм и
заводится, поэтому смотреть и скачивать может любой сотрудник.

Грифа «ДСП» у бланка нет — в модели `Template` такого поля не
существует, в отличие от `NormativeDocument.access_level`. Это не
упущение слоя прав, а свойство схемы: если Заказчик потребует
закрытых форм, поле придётся заводить в модели, и тогда сюда добавится
проверка, симметричная `documents.can_view_dsp`.
"""
from __future__ import annotations


def _manager_roles():
    from apps.iam.models import User

    return {User.Role.CONTROLLER_LAWYER, User.Role.ADMINISTRATOR}


def can_view_templates(user) -> bool:
    return bool(getattr(user, "is_authenticated", False))


def can_manage_templates(user) -> bool:
    """Публикация новой версии бланка и переиздание (ТЗ 4.3.1)."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.role in _manager_roles()
