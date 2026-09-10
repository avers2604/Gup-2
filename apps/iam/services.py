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
from django.contrib.auth import authenticate
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.audit.models import AuditLog

from .models import Department, User
from .totp import generate_totp_secret, totp_provisioning_uri, verify_totp_code

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
        try:
            # .get(), не filter().first(): UniqueConstraint(name, parent)
            # на Department (iam.0004) гарантирует не более одной строки
            # для parent != NULL, так что MultipleObjectsReturned здесь —
            # не гипотетический случай "на всякий", а сигнал реального
            # повреждения данных в обход этого констрейнта (например,
            # прямым SQL) — не должен тихо резолвиться в первую попавшуюся
            # запись через first(). Для уровня 1 (parent=NULL) констрейнт
            # НЕ защищает (см. Department.Meta) — MultipleObjectsReturned
            # там всё ещё теоретически возможен и обрабатывается так же,
            # явной ошибкой, а не first().
            department = Department.objects.get(name=segment, parent=parent)
        except Department.DoesNotExist:
            raise ValueError(f"Подразделение «{segment}» не найдено в пути «{path}».")
        except Department.MultipleObjectsReturned:
            raise ValueError(
                f"Подразделение «{segment}» в пути «{path}» неоднозначно "
                "(несколько узлов с одинаковым именем у одного родителя) — "
                "требуется ручное исправление оргструктуры."
            )
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
        # Гонка параллельного импорта: между проверкой full_clean() (своим
        # SELECT) и реальным INSERT другая транзакция успела создать
        # запись с тем же tab_number — уникальный констрейнт БД
        # (iam_user_personnel_number_key) отработал как задумано. Строка
        # помечается ошибкой вместо падения всего импорта — оператор
        # перезапустит именно её.
        return "Табельный номер уже создан параллельной операцией импорта — повторите загрузку этой строки."
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

    row_iter = sheet.iter_rows(values_only=True)
    try:
        raw_header = next(row_iter)
    except StopIteration:
        raise ValidationError("Файл пуст.")

    header = [str(c).strip() if c is not None else "" for c in raw_header]
    data_rows = []
    for row in row_iter:
        if any(c is not None and str(c).strip() != "" for c in row):
            if len(data_rows) >= MAX_ROWS:
                raise ValidationError(
                    f"В файле больше {MAX_ROWS} строк — максимум за одну операцию (ТЗ 4.6)."
                )
            data_rows.append(row)

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
        except (ValueError, ValidationError, IntegrityError) as exc:
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


# --- Вход и 2FA/TOTP (ТЗ 4.7) --------------------------------------------
#
# Общая бизнес-логика для ДВУХ независимых HTTP-контуров (решение
# Заказчика): Web GUI (apps/iam/views.py, серверный рендеринг, сессия) и
# External API (apps/iam/api.py, DRF + JWT). Ни views.py, ни api.py не
# обращаются к User.objects/verify_totp_code напрямую — только через
# функции этого раздела, чтобы правила (кто должен пройти 2FA, что
# считается верным кодом, что пишется в аудит) не разъехались между
# контурами.
#
# Двухшаговый вход одинаков в обоих контурах: шаг 1 проверяет пароль и,
# если у пользователя включена 2FA, возвращает подписанный "pending"-
# тикет вместо готовой авторизации — сама авторизация (django_login() в
# Web, выдача JWT в API) происходит только в шаге 2, после верного кода.
# Тикет подписан через django.core.signing (не Django-сессия): так шаг 2
# одинаково работает и для контура с cookie (Web), и для контура без
# состояния на сервере (API/JWT) — не пришлось заводить два разных
# механизма "ожидания кода" под два контура.

_TOTP_PENDING_TICKET_SALT = "apps.iam.services.totp_pending_ticket"
_TOTP_PENDING_TICKET_MAX_AGE = 5 * 60  # 5 минут на ввод кода после шага 1


class TotpEnrollmentNotStarted(Exception):
    """confirm_totp_enrollment() вызван раньше start_totp_enrollment()."""


@dataclass
class CredentialCheckResult:
    user: User
    totp_required: bool


def check_credentials(request, *, personnel_number: str, password: str) -> CredentialCheckResult | None:
    """Шаг 1. None — неверный табельный номер, неверный пароль или
    пользователь заблокирован (is_active=False уже отсекается
    authenticate() через ModelBackend) — вызывающий код должен отвечать
    ОДНИМ сообщением на все три случая, не раскрывая, какой именно."""
    user = authenticate(request, username=personnel_number, password=password)
    if user is None:
        return None
    return CredentialCheckResult(user=user, totp_required=user.totp_enabled)


def make_totp_pending_ticket(user: User) -> str:
    return signing.dumps({"user_id": str(user.pk)}, salt=_TOTP_PENDING_TICKET_SALT)


def verify_totp_login(*, ticket: str, code: str) -> User | None:
    """Шаг 2. None — тикет невалиден/подделан/просрочен, пользователя уже
    нет, он не активен (заблокирован между шагом 1 и шагом 2 — окно
    небольшое, но не нулевое), или код неверный."""
    try:
        data = signing.loads(ticket, salt=_TOTP_PENDING_TICKET_SALT, max_age=_TOTP_PENDING_TICKET_MAX_AGE)
    except signing.BadSignature:
        return None

    user = User.objects.filter(pk=data.get("user_id")).first()
    if user is None or not user.is_active:
        return None
    if not verify_totp_code(secret=user.totp_secret, code=code):
        return None
    return user


def user_auth_summary(user: User) -> dict:
    """Общий вид ответа после успешного входа — одинаковый в Web (JSON
    для HTMX-фрагмента/редиректа) и API (тело JWT-ответа)."""
    return {
        "personnel_number": user.personnel_number,
        "full_name": user.full_name,
        "role": user.role,
        "totp_enabled": user.totp_enabled,
        # Роль требует 2FA (ТЗ 4.7), но enroll ещё не пройден — сигнал
        # клиенту направить пользователя на start_totp_enrollment(),
        # не блокировка самого входа.
        "must_enroll_totp": user.requires_totp and not user.totp_enabled,
        "password_change_required": user.status == User.Status.PASSWORD_CHANGE_REQUIRED,
    }


def record_session_login(user: User) -> None:
    AuditLog.objects.create(
        event_type=AuditLog.EventType.SESSION_LOGIN,
        actor=user, actor_personnel_number=user.personnel_number,
        object_type="User", object_id=str(user.pk),
    )


def record_session_logout(user: User) -> None:
    AuditLog.objects.create(
        event_type=AuditLog.EventType.SESSION_LOGOUT,
        actor=user, actor_personnel_number=user.personnel_number,
        object_type="User", object_id=str(user.pk),
    )


def start_totp_enrollment(user: User) -> dict:
    """Генерирует новый секрет, но НЕ включает totp_enabled — включение
    только через confirm_totp_enrollment(), иначе пользователь рискует
    остаться без доступа, так и не убедившись, что приложение-
    аутентификатор реально синхронизировано с сервером."""
    secret = generate_totp_secret()
    user.totp_secret = secret
    user.save(update_fields=["totp_secret"])
    return {
        "secret": secret,
        "provisioning_uri": totp_provisioning_uri(secret=secret, personnel_number=user.personnel_number),
    }


def confirm_totp_enrollment(user: User, *, code: str) -> bool:
    """True — 2FA включена. False — неверный код (можно повторить).
    Поднимает TotpEnrollmentNotStarted, если start_totp_enrollment() ещё
    не вызывался — это ошибка порядка вызовов на стороне клиента, не
    "неверный код"."""
    if not user.totp_secret:
        raise TotpEnrollmentNotStarted
    if not verify_totp_code(secret=user.totp_secret, code=code):
        return False
    user.totp_enabled = True
    user.save(update_fields=["totp_enabled"])
    return True
