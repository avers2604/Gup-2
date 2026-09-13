"""Сортировка списков по клику на заголовок колонки.

Разрешённые колонки перечисляются явным белым списком, а не берутся из
параметра запроса. Причина не только в инъекции: `order_by` принимает
пути по связям, и `?sort=issuer_dept__head__password` — вполне рабочая
сортировка, по которой значения соседних строк сравниваются между собой.
Порядок строк на странице выдаёт больше, чем кажется.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Направление сортировки в параметре запроса кодируется префиксом «-» —
#: тем же, что и в Django ORM, чтобы в ссылке было видно то же самое, что
#: в коде.
DESC_PREFIX = "-"


@dataclass(frozen=True)
class Column:
    """Колонка, по которой разрешено сортировать.

    `fields` — кортеж, а не одно поле: осмысленная сортировка по виду
    документа или по подразделению внутри группы всё равно должна быть
    устойчивой, иначе строки прыгают между страницами при пагинации.
    """

    key: str
    label: str
    fields: tuple[str, ...]


def resolve(columns: dict[str, Column], requested: str | None, default_order: tuple[str, ...]):
    """(ключ, по_убыванию, поля_для_order_by) для запрошенной сортировки.

    `default_order` — готовый порядок ORM, а не ключ колонки: список может
    по умолчанию сортироваться по полю, которого в таблице нет (реестр НРД
    показывает дату вступления в силу, а упорядочен по дате регистрации).
    В этом случае возвращается пустой ключ, и ни один заголовок не
    помечается активным — что честно: пользователь этот порядок не
    выбирал.

    Неизвестный ключ молча заменяется порядком по умолчанию: параметр
    приходит из ссылки, которую легко испортить копированием, и ронять
    из-за него страницу реестра незачем.
    """
    requested = (requested or "").strip()
    descending = requested.startswith(DESC_PREFIX)
    key = requested[1:] if descending else requested

    if key not in columns:
        return "", False, default_order

    prefix = DESC_PREFIX if descending else ""
    return key, descending, tuple(f"{prefix}{field}" for field in columns[key].fields)


def header_links(columns: dict[str, Column], active_key: str, descending: bool, query: str):
    """Описания заголовков для шаблона: подпись, ссылка и aria-sort.

    Ссылка на активной колонке переключает направление, на остальных —
    задаёт возрастание. `aria-sort` обязателен: без него скринридер не
    сообщает, что таблица вообще отсортирована, и стрелка в заголовке
    остаётся чисто зрительным признаком.
    """
    rows = []
    for key, column in columns.items():
        is_active = key == active_key
        # На активной колонке ссылка ведёт в противоположную сторону.
        target = f"{DESC_PREFIX}{key}" if is_active and not descending else key
        parts = [part for part in (query, f"sort={target}") if part]
        rows.append({
            "key": key,
            "label": column.label,
            "url": "?" + "&".join(parts),
            "is_active": is_active,
            "descending": is_active and descending,
            "aria_sort": ("descending" if descending else "ascending") if is_active else "none",
        })
    return rows
