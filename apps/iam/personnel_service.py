"""Personnel import application service.

Separated from authentication/TOTP to keep IAM use-cases isolated and easy
to test. ``apps.iam.services`` re-exports this API for compatibility.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.audit.models import AuditLog

from .models import Department, User

MAX_ROWS = 5000
HEADER = [
    "tab_number", "last_name", "first_name", "middle_name", "position",
    "department_path", "role", "dsp_access", "email", "status",
]
REQUIRED_COLUMNS = {
    "tab_number", "last_name", "first_name", "position",
    "department_path", "role", "dsp_access",
}
ROLE_IMPORT_MAP = {
    "reader": User.Role.READER,
    "methodist": User.Role.METHODIST,
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
    def total(self) -> int:
        return len(self.successes) + len(self.updates) + len(self.errors)


def _resolve_department(path: str) -> Department:
    segments = [segment.strip() for segment in path.split("/") if segment.strip()]
    if not segments:
        raise ValueError("department_path пуст.")
    parent = None
    department = None
    for segment in segments:
        try:
            department = Department.objects.get(name=segment, parent=parent)
        except Department.DoesNotExist as exc:
            raise ValueError(f"Подразделение «{segment}» не найдено в пути «{path}».") from exc
        except Department.MultipleObjectsReturned as exc:
            raise ValueError(f"Подразделение «{segment}» в пути «{path}» неоднозначно.") from exc
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
    if isinstance(exc, IntegrityError):
        return "Табельный номер уже создан параллельной операцией импорта — повторите загрузку этой строки."
    return str(exc)


def _cell(raw_row, col_index, name):
    index = col_index.get(name)
    if index is None or index >= len(raw_row):
        return None
    return raw_row[index]


def import_personnel(file_obj, *, actor: User | None = None) -> ImportReport:
    workbook = openpyxl.load_workbook(file_obj, data_only=True, read_only=True)
    sheet = workbook.active
    row_iter = sheet.iter_rows(values_only=True)
    try:
        raw_header = next(row_iter)
    except StopIteration as exc:
        raise ValidationError("Файл пуст.") from exc

    header = [str(cell).strip() if cell is not None else "" for cell in raw_header]
    missing_columns = REQUIRED_COLUMNS - set(header)
    if missing_columns:
        raise ValidationError(
            f"В файле отсутствуют обязательные колонки: {', '.join(sorted(missing_columns))}."
        )

    data_rows = []
    for row in row_iter:
        if not any(cell is not None and str(cell).strip() for cell in row):
            continue
        if len(data_rows) >= MAX_ROWS:
            raise ValidationError(f"В файле больше {MAX_ROWS} строк — максимум за одну операцию (ТЗ 4.6).")
        data_rows.append(row)

    col_index = {name: index for index, name in enumerate(header)}
    report = ImportReport()
    for row_number, raw_row in enumerate(data_rows, start=2):
        tab_number = str(_cell(raw_row, col_index, "tab_number") or "").strip()
        try:
            with transaction.atomic():
                if not tab_number:
                    raise ValueError("tab_number не заполнен.")
                department = _resolve_department(str(_cell(raw_row, col_index, "department_path") or "").strip())
                role_raw = str(_cell(raw_row, col_index, "role") or "").strip()
                if role_raw not in ROLE_IMPORT_MAP:
                    raise ValueError(f"Неизвестная роль «{role_raw}». Допустимо: {', '.join(ROLE_IMPORT_MAP)}.")
                role = ROLE_IMPORT_MAP[role_raw]
                status_raw = str(_cell(raw_row, col_index, "status") or "").strip().lower() or "active"
                if status_raw not in STATUS_IMPORT_MAP:
                    raise ValueError(f"Неизвестный статус «{status_raw}». Допустимо: {', '.join(STATUS_IMPORT_MAP)}.")
                status = STATUS_IMPORT_MAP[status_raw]
                dsp_access = _parse_bool(_cell(raw_row, col_index, "dsp_access"), field_name="dsp_access")

                existing = User.objects.filter(personnel_number=tab_number).first()
                is_update = existing is not None
                previous_role = existing.role if existing else None
                user = existing or User(personnel_number=tab_number)
                if existing is None:
                    user.set_unusable_password()

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
                user._audit_actor = actor
                user.save()

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
        except (ValueError, ValidationError, IntegrityError) as exc:
            report.errors.append(ImportRowResult(row_number, tab_number, _format_error(exc)))
            continue

        (report.updates if is_update else report.successes).append(ImportRowResult(row_number, tab_number))
    return report


def write_report_csv(report: ImportReport, out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    sections = [("success", report.successes), ("updated", report.updates), ("errors", report.errors)]
    for name, rows in sections:
        path = out_dir / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream, delimiter=";")
            if name == "errors":
                writer.writerow(["Строка", "Табельный номер", "Ошибка"])
                for row in rows:
                    writer.writerow([row.row_number, row.tab_number, row.detail])
            else:
                writer.writerow(["Строка", "Табельный номер"])
                for row in rows:
                    writer.writerow([row.row_number, row.tab_number])
        paths[name] = path
    return paths
