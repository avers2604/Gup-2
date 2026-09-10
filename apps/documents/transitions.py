"""Допустимые переходы статуса карточки НРД (ТЗ 4.2.3).

ОТКУДА ВЗЯТ ГРАФ. Перечень статусов задан ТЗ, допустимые переходы между
ними — нет. Первая редакция графа была выведена из смысла статусов и
помечена как требующая подтверждения; **Заказчик её рассмотрел и уточнил
двумя решениями**, которые и отражены ниже:

1. **Ошибочная публикация оформляется двумя разными путями**, а не одним.
   «Утратил силу» для этого не годится: он утверждает, что документ
   действовал и перестал, — а при ошибке публикации он не должен был
   действовать вовсе, и такая запись искажает юридическую историю.
   Поэтому:
   - `ACTIVE -> DRAFT` — **откат**: публикация была преждевременной или
     ушла не с тем файлом, документ вернётся в работу и будет издан
     заново;
   - `ACTIVE -> ANNULLED` — **аннулирование**: публикация признана
     недействительной, документ в работу не возвращается, карточка
     остаётся записью о самом факте.
2. **`ACTIVE_AMENDED -> ACTIVE`** — если все изменяющие документы
   отменены, базовый документ снова действует в исходной редакции.
   Ситуация обычная, и раньше выхода из «Действует с изм.» кроме утраты
   силы не было.

Остальное — как в первой редакции и по-прежнему по выводу, а не по
цитате: `DRAFT -> ARCHIVED` для отклонённого черновика (удаления в
системе с WORM-журналом нет, иначе такая карточка висела бы в реестре
вечно), `ARCHIVED` — терминальное состояние, прямого `ACTIVE -> ARCHIVED`
нет (действующий документ в архив не сдаётся).

Тесты `test_transitions.py` фиксируют граф как контракт: если Заказчик
уточнит его ещё раз, они меняются вместе с ним.
"""
from __future__ import annotations

from .models import NormativeDocument

Status = NormativeDocument.Status

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    Status.DRAFT: frozenset({Status.ACTIVE, Status.ARCHIVED}),
    Status.ACTIVE: frozenset({
        Status.ACTIVE_AMENDED, Status.REVOKED, Status.DRAFT, Status.ANNULLED,
    }),
    Status.ACTIVE_AMENDED: frozenset({Status.ACTIVE, Status.REVOKED, Status.ANNULLED}),
    Status.REVOKED: frozenset({Status.ARCHIVED}),
    Status.ANNULLED: frozenset({Status.ARCHIVED}),
    Status.ARCHIVED: frozenset(),
}

# Переходы, придающие документу юридическую силу. Только они запускают
# проверку графа связей на ацикличность (ТЗ 4.2.2) — см.
# services.change_document_status().
PUBLISHING_TRANSITIONS = frozenset({Status.ACTIVE})

# Исправление ошибки публикации: откат в черновик и аннулирование.
# Доступны только Администратору, а не Контролёру/Юристу, который
# публикует. Причина — разделение обязанностей: оба перехода отменяют
# юридически значимое действие, и тот, кто его совершил, не должен иметь
# возможности бесследно поправить собственную ошибку. **Это вывод, а не
# цитата из ТЗ, и подлежит подтверждению Заказчиком** — как и весь граф.
ADMINISTRATOR_ONLY_TARGETS = frozenset({Status.DRAFT, Status.ANNULLED})

# Те же переходы требуют заполненного основания: запись «публикация
# отменена» без объяснения бесполезна и проверяющему, и самому
# подразделению через полгода.
REASON_REQUIRED_TARGETS = ADMINISTRATOR_ONLY_TARGETS


def allowed_targets(current_status: str) -> frozenset[str]:
    """Статусы, в которые можно перейти из текущего. Неизвестный статус
    даёт пустое множество, а не KeyError: перечень статусов может
    расшириться миграцией раньше, чем сюда допишут его переходы, и в этом
    случае безопаснее запретить всё, чем разрешить что-нибудь наугад."""
    return ALLOWED_TRANSITIONS.get(current_status, frozenset())


def is_allowed(current_status: str, new_status: str) -> bool:
    return new_status in allowed_targets(current_status)


def requires_administrator(new_status: str) -> bool:
    return new_status in ADMINISTRATOR_ONLY_TARGETS


def requires_reason(new_status: str) -> bool:
    return new_status in REASON_REQUIRED_TARGETS


def target_choices(current_status: str) -> list[tuple[str, str]]:
    """Пары (значение, человекочитаемое название) для выпадающего списка.
    Порядок — как в Status.choices, чтобы форма не тасовала варианты от
    запроса к запросу (frozenset неупорядочен)."""
    targets = allowed_targets(current_status)
    return [(value, label) for value, label in Status.choices if value in targets]
