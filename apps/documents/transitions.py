"""Допустимые переходы статуса карточки НРД (ТЗ 4.2.3).

ОТКУДА ВЗЯТ ГРАФ (важно для ревью Заказчиком). Как и матрица прав в
`permissions.py`, этот граф **выведен**, а не процитирован: перечень
статусов задан ТЗ (`NormativeDocument.Status`), но допустимые переходы
между ними поимённо не расписаны. Основания:

1. `DRAFT -> ACTIVE` — публикация, придание юридической силы. Единственный
   переход, прямо подразумеваемый формулировкой ТЗ 4.2.2 «Алгоритм
   публикации проверяет граф связей на ацикличность».
2. `ACTIVE -> ACTIVE_AMENDED` — в документ внесены изменения другим НРД.
   Обратного перехода нет: изменения не «отменяются» возвратом статуса,
   отмена изменяющего документа оформляется своей связью версионности.
3. `ACTIVE`/`ACTIVE_AMENDED -> REVOKED` — документ утратил силу.
4. `REVOKED -> ARCHIVED` — передача в архив после утраты силы.
5. `DRAFT -> ARCHIVED` — отклонённый черновик. Иначе у черновика, который
   так и не был издан, нет ни одного выхода: удаление в системе с WORM-
   журналом не предусмотрено, и такая карточка висела бы в реестре вечно.

Чего в графе намеренно НЕТ:

- **Возврата из `ACTIVE` в `DRAFT`.** Снятие юридической силы — это
  `REVOKED`, а не откат в черновик: иначе история статусов (ТЗ 4.2.3)
  описывала бы документ, который «побыл действующим и перестал им быть,
  не будучи отменённым».
- **Прямого `ACTIVE -> ARCHIVED`.** Действующий документ в архив не
  сдаётся — сначала он должен утратить силу.
- **Любых переходов из `ARCHIVED`.** Терминальное состояние.

**Требует подтверждения Заказчиком** — это интерпретация. Спорные места
меняются здесь, в одном месте; тесты `test_transitions.py` фиксируют граф
как контракт и должны меняться вместе с ним.
"""
from __future__ import annotations

from .models import NormativeDocument

Status = NormativeDocument.Status

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    Status.DRAFT: frozenset({Status.ACTIVE, Status.ARCHIVED}),
    Status.ACTIVE: frozenset({Status.ACTIVE_AMENDED, Status.REVOKED}),
    Status.ACTIVE_AMENDED: frozenset({Status.REVOKED}),
    Status.REVOKED: frozenset({Status.ARCHIVED}),
    Status.ARCHIVED: frozenset(),
}

# Переходы, придающие документу юридическую силу. Только они запускают
# проверку графа связей на ацикличность (ТЗ 4.2.2) — см.
# services.change_document_status().
PUBLISHING_TRANSITIONS = frozenset({Status.ACTIVE})


def allowed_targets(current_status: str) -> frozenset[str]:
    """Статусы, в которые можно перейти из текущего. Неизвестный статус
    даёт пустое множество, а не KeyError: перечень статусов может
    расшириться миграцией раньше, чем сюда допишут его переходы, и в этом
    случае безопаснее запретить всё, чем разрешить что-нибудь наугад."""
    return ALLOWED_TRANSITIONS.get(current_status, frozenset())


def is_allowed(current_status: str, new_status: str) -> bool:
    return new_status in allowed_targets(current_status)


def target_choices(current_status: str) -> list[tuple[str, str]]:
    """Пары (значение, человекочитаемое название) для выпадающего списка.
    Порядок — как в Status.choices, чтобы форма не тасовала варианты от
    запроса к запросу (frozenset неупорядочен)."""
    targets = allowed_targets(current_status)
    return [(value, label) for value, label in Status.choices if value in targets]
