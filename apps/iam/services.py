"""
Пакетный импорт персонала из Excel (ТЗ 4.6: «Первичная и периодическая
загрузка сотрудников из кадровых таблиц Excel/CSV, до 5 000 учётных
записей»). Формат колонок и правила обработки — по присланному образцу
файла, не придуманы отдельно от него:

- дубль по tab_number -> upsert (обновление существующей записи, не
  создание новой);
- department_path, не найденный в дереве оргструктуры -> строка
  отклоняется с указанием ошибки, импорт остальных строк продолжается;
- повышение роли у существующего пользователя -> отдельная запись в
  WORM-журнале аудита (apps.audit.AuditLog);
- максимум 5000 строк за одну операцию;
- массовое удаление через импорт НЕ выполняется ни при каких условиях —
  строка, отсутствующая в файле, не трогается; единственный способ
  деактивировать учётную запись — явно указать в файле status=blocked
  для конкретного tab_number.
"""
import csv
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditLog

from .models import Department, User

MAX_ROWS = 5000

HEADER = [
    "tab_number", "last_name", "first_name", "middle_name", "position",
    "department_path", "role", "dsp_access", "email", "status",
]
REQUIRED_COLUMNS = {
    "tab_number", "last_name", "first_name", "position", "department_path", "role", "dsp_access",
}

ROLE_IMPORT_MAP = {
    "reader": User.Role.READER,
    "curator": User.Role.CURATOR,
    "controller": User.Role.CONTROLLER_LAWYER,
    "security_officer": User.Role.SECURITY_OFFICER,
    "admin": User.Role.ADMINISTRATOR,
}

STATUS_IMPORT_MAP = {
    "active": User.Status.ACTIVE,
    "blocked": User.Status.BLOCKED,
    "password_change_required": User.Status.PASSWORD_CHANGE_REQUIRED,
}

_TRUE_VALUES = {"true", "1", "да", "истина"}
_FALSE_VALUES = {"false", "0", "нет", "ложь", ""}


@dataclass
class ImportRowResult:
    row_number: int
    tab_number: str
    detail: str = ""


@dataclass
class ImportReport:
    successes: list[ImportRowResult] = field(default_factory=list)
    updates: list[ImportRowResult] = field(default_factory=list)
    errors: list[ImportRowResult] = field(default_factory=list)

    @property
    def total(self):
        return len(self.successes) + len(self.updates) + len(self.errors)


def _resolve_department(path: str) -> Department:
    segments = [s.strip() for s in path.split("/") if s.strip()]
    if not segments:
        raise ValueError("department_path пуст.")
    parent = None
    department = None
    for segment in segments:
        department = Department.objects.filter(name=segment, parent=parent).first()
        if department is None:
            raise ValueError(f"Подразделение «{segment}» не найдено в пути «{path}».")
        parent = department
    return department


def _parse_bool(value, *, field_name: str) -> bool:
    text = str(value).strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    raise ValueError(f"Некорректное булево значение поля {field_name}: {value!r}.")


def _is_role_elevated(previous_role: str, new_role: str) -> bool:
    order = User.ROLE_PRIVILEGE_ORDER
    try:
        return order.index(new_role) > order.index(previous_role)
    except ValueError:
        return False


def _format_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(exc.messages)
    return str(exc)


def _cell(raw_row, col_index, name):
    i = col_index.get(name)
    if i is None or i >= len(raw_row):
        return None
    return raw_row[i]


def import_personnel(file_obj, *, actor: User | None = None) -> ImportReport:
    """file_obj — путь или файлоподобный объект, читаемый openpyxl (.xlsx)."""
    workbook = openpyxl.load_workbook(file_obj, data_only=True, read_only=True)
    sheet = workbook.active

    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        raise ValidationError("Файл пуст.")

    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    data_rows = [r for r in rows[1:] if any(c is not None and str(c).strip() != "" for c in r)]

    if len(data_rows) > MAX_ROWS:
        raise ValidationError(
            f"В файле {len(data_rows)} строк — максимум {MAX_ROWS} за одну операцию (ТЗ 4.6)."
        )

    missing_columns = REQUIRED_COLUMNS - set(header)
    if missing_columns:
        raise ValidationError(
            f"В файле отсутствуют обязательные колонки: {', '.join(sorted(missing_columns))}."
        )

    col_index = {name: i for i, name in enumerate(header)}
    report = ImportReport()

    for row_number, raw_row in enumerate(data_rows, start=2):
        tab_number = str(_cell(raw_row, col_index, "tab_number") or "").strip()
        try:
            with transaction.atomic():
                if not tab_number:
                    raise ValueError("tab_number не заполнен.")

                department = _resolve_department(
                    str(_cell(raw_row, col_index, "department_path") or "").strip()
                )

                role_raw = str(_cell(raw_row, col_index, "role") or "").strip()
                if role_raw not in ROLE_IMPORT_MAP:
                    raise ValueError(
                        f"Неизвестная роль «{role_raw}». Допустимо: {', '.join(ROLE_IMPORT_MAP)}."
                    )
                role = ROLE_IMPORT_MAP[role_raw]

                status_raw = str(_cell(raw_row, col_index, "status") or "").strip().lower() or "active"
                if status_raw not in STATUS_IMPORT_MAP:
                    raise ValueError(
                        f"Неизвестный статус «{status_raw}». Допустимо: {', '.join(STATUS_IMPORT_MAP)}."
                    )
                status = STATUS_IMPORT_MAP[status_raw]

                dsp_access = _parse_bool(_cell(raw_row, col_index, "dsp_access"), field_name="dsp_access")

                existing = User.objects.filter(personnel_number=tab_number).first()
                is_update = existing is not None
                previous_role = existing.role if existing else None

                if existing is None:
                    user = User(personnel_number=tab_number)
                    user.set_unusable_password()
                else:
                    user = existing

                user.last_name = str(_cell(raw_row, col_index, "last_name") or "").strip()
                user.first_name = str(_cell(raw_row, col_index, "first_name") or "").strip()
                user.middle_name = str(_cell(raw_row, col_index, "middle_name") or "").strip()
                user.position = str(_cell(raw_row, col_index, "position") or "").strip()
                user.department = department
                user.role = role
                user.dsp_access = dsp_access
                user.email = str(_cell(raw_row, col_index, "email") or "").strip()
                user.status = status

                user.full_clean(exclude=["password"])
                user.save()

                # Массовое удаление через импорт не выполняется: строки,
                # отсутствующие в файле, здесь никак не затрагиваются —
                # цикл работает только с tab_number, реально присутствующими
                # в текущем файле. Единственный способ деактивировать
                # учётную запись — status=blocked явно в файле (выше).
                if is_update and _is_role_elevated(previous_role, role):
                    AuditLog.objects.create(
                        event_type=AuditLog.EventType.USER_ROLE_ELEVATED,
                        actor=actor,
                        actor_personnel_number=actor.personnel_number if actor else "",
                        object_type="User",
                        object_id=str(user.pk),
                        details={
                            "target_personnel_number": tab_number,
                            "previous_role": previous_role,
                            "new_role": role,
                            "source": "personnel_import",
                        },
                    )
        except (ValueError, ValidationError) as exc:
            report.errors.append(ImportRowResult(row_number, tab_number, _format_error(exc)))
            continue

        if is_update:
            report.updates.append(ImportRowResult(row_number, tab_number))
        else:
            report.successes.append(ImportRowResult(row_number, tab_number))

    return report


def write_report_csv(report: ImportReport, out_dir: Path) -> dict[str, Path]:
    """Три отдельных CSV — success/updated/errors (точки-в-разрезе результата
    импорта, а не листы одного файла: у CSV нет листов). ; как разделитель
    и BOM (utf-8-sig) — чтобы Excel в русской локали открывал файл сразу
    корректно, без ручного выбора кодировки."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    sections = [("success", report.successes), ("updated", report.updates), ("errors", report.errors)]
    for name, rows in sections:
        path = out_dir / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            if name == "errors":
                writer.writerow(["Строка", "Табельный номер", "Ошибка"])
                for r in rows:
                    writer.writerow([r.row_number, r.tab_number, r.detail])
            else:
                writer.writerow(["Строка", "Табельный номер"])
                for r in rows:
                    writer.writerow([r.row_number, r.tab_number])
        paths[name] = path
    return paths
