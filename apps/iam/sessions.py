"""
Принудительный сброс сессий при блокировке пользователя (ТЗ 4.7:
«принудительный сброс сессий при блокировке пользователя»).

Django не делает этого сам: is_active влияет только на момент логина
(ModelBackend.user_can_authenticate), уже открытая сессия остаётся
рабочей до истечения срока, если её не удалить явно. Реализовано через
стандартную таблицу django.contrib.sessions — так как проект использует
её штатный DB-backed движок (SESSION_ENGINE не переопределён,
apps.core/config не подключают ничего другого).

Это O(число активных сессий) — расшифровка каждой записи, поскольку
django.contrib.sessions не индексирует данные сессии по user_id. Для
небольшого штата (до нескольких тысяч одновременных сессий, ТЗ 4.6) это
приемлемо; при заметном росте потребуется отдельная индексация
(например, таблица session_key -> user_id, обновляемая при логине) —
не реализовано, так как сейчас нет ни этой нагрузки, ни самого механизма
логина (Этап 3)."""
from django.contrib.sessions.models import Session
from django.utils import timezone


def force_logout_user(user_id) -> int:
    """Удаляет все активные (ещё не истёкшие) сессии пользователя.
    Возвращает число удалённых сессий."""
    user_id = str(user_id)
    deleted = 0
    for session in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
        data = session.get_decoded()
        if str(data.get("_auth_user_id")) == user_id:
            session.delete()
            deleted += 1
    return deleted
