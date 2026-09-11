"""
Матрица сроков хранения и режимов Object Locking (WORM) — по данным,
присланным Заказчиком (закрывает открытые вопросы №1-2 плана работ:
«срок хранения архивных сканов НРД», «Governance или Compliance»).

Это юридическая классификация (основание — конкретные статьи ФЗ, перечни
Росархива), а не техническая эвристика, поэтому retention_category
указывается явно при регистрации документа (Контролёром/Юристом), а
не выводится молча из типа документа — присвоение неверной категории
имеет юридические последствия, автоматика здесь неуместна. Там, где
категория однозначно следует из уже существующих полей карточки
(access_level=ДСП, скан старше 2017 года), это подсказывается как
значение по умолчанию (suggest_retention_category), но не подменяет
ответственность за выбор.

Строки «Акты расследований ДТП, сходов, Н-1» и «Наряды-допуски,
протоколы испытаний ЭХ» из присланной матрицы пока не привязаны ни к
одной модели — это заполняемые через будущий «Мастер заполнения» акты.
Категории для них уже заведены здесь (ACTS_INVESTIGATION, PERMITS_EH),
чтобы при реализации не пришлось переносить матрицу заново.

Категория TEMPLATES_APPROVED уже подключена к опубликованным Template:
каждая версия бланка — отдельный immutable artifact. Новые байты сначала
попадают в mutable staging, а после commit PostgreSQL копируются в
`originals` с Governance/Legal Hold через apps.core.staged_files. Тем же
механизмом для NormativeDocument.files_original фактически применяются
параметры этой матрицы к версии объекта в MinIO, а не только сохраняются
как метаданные в БД.
"""
import datetime

from django.db import models


class RetentionMode(models.TextChoices):
    GOVERNANCE = "governance", "Governance (снимается Офицером ИБ + Контролёром)"
    COMPLIANCE = "compliance", "Compliance (не снимается никем, включая root)"


class RetentionCategory(models.TextChoices):
    ORDERS_CORE = "orders_core", "Приказы по основной деятельности"
    ORDERS_PERSONNEL = "orders_personnel", "Приказы по личному составу"
    DIRECTIVES_OPERATIONAL = "directives_operational", "Распоряжения оперативного характера"
    ACTS_INVESTIGATION = "acts_investigation", "Акты расследований ДТП, сходов, Н-1"
    PERMITS_EH = "permits_eh", "Наряды-допуски, протоколы испытаний ЭХ"
    TEMPLATES_APPROVED = "templates_approved", "Бланки и типовые формы (утверждённые)"
    ARCHIVAL_SCANS = "archival_scans", "Архивные сканы (до 2017 г.)"
    DSP = "dsp", "Документы ДСП"


# Категории, применимые к карточке НРД (NormativeDocument) уже сейчас.
# ACTS_INVESTIGATION/PERMITS_EH оставлены для ещё не реализованной модели
# актов; TEMPLATES_APPROVED применяется моделью Template отдельно.
NORMATIVE_DOCUMENT_CATEGORIES = {
    RetentionCategory.ORDERS_CORE,
    RetentionCategory.ORDERS_PERSONNEL,
    RetentionCategory.DIRECTIVES_OPERATIONAL,
    RetentionCategory.ARCHIVAL_SCANS,
    RetentionCategory.DSP,
}


class RetentionPolicy:
    __slots__ = (
        "category", "mode", "period_years", "conditional_on_declassification", "legal_basis",
        "is_active", "stage",
    )

    def __init__(
        self, category, mode, period_years, conditional_on_declassification, legal_basis,
        *, is_active=True, stage=None,
    ):
        self.category = category
        self.mode = mode
        self.period_years = period_years  # None = «постоянно»
        self.conditional_on_declassification = conditional_on_declassification
        self.legal_basis = legal_basis
        # is_active=False — категория заведена в матрице (данные присланы
        # Заказчиком), но ни одна модель ещё не пишет её на реальные записи:
        # ни один экран/API не должен предлагать её к выбору, чтобы её
        # нельзя было присвоить документу «случайно» до того, как для неё
        # реализована сама карточка. stage — человекочитаемая пометка, когда
        # это ожидается.
        self.is_active = is_active
        self.stage = stage


# Данные — дословно из присланной матрицы (типичный срок / режим WORM / основание).
RETENTION_MATRIX = {
    RetentionCategory.ORDERS_CORE: RetentionPolicy(
        RetentionCategory.ORDERS_CORE, RetentionMode.COMPLIANCE, None, False,
        "Перечень Росархива, ст. 22.1 ФЗ-125 (постоянно/10 лет — принято постоянно как консервативный вариант)",
    ),
    RetentionCategory.ORDERS_PERSONNEL: RetentionPolicy(
        RetentionCategory.ORDERS_PERSONNEL, RetentionMode.COMPLIANCE, 50, False,
        "Ст. 22.1 ФЗ-125 (документы после 2003 г. — 50 лет; до 2003 г. — 75 лет, см. note ниже)",
    ),
    RetentionCategory.DIRECTIVES_OPERATIONAL: RetentionPolicy(
        RetentionCategory.DIRECTIVES_OPERATIONAL, RetentionMode.GOVERNANCE, 5, False,
        "Перечень Росархива (5 лет ЭПК — экспертно-проверочная комиссия)",
    ),
    RetentionCategory.ACTS_INVESTIGATION: RetentionPolicy(
        RetentionCategory.ACTS_INVESTIGATION, RetentionMode.COMPLIANCE, 45, False,
        "ФЗ-125, требования Ространснадзора (45/75 лет — принято 45 как консервативный минимум)",
        is_active=False, stage="Этап 2 (Мастер заполнения актов)",
    ),
    RetentionCategory.PERMITS_EH: RetentionPolicy(
        RetentionCategory.PERMITS_EH, RetentionMode.GOVERNANCE, 10, False,
        "ПТЭ, требования Ростехнадзора",
        is_active=False, stage="Этап 2 (Мастер заполнения актов)",
    ),
    RetentionCategory.TEMPLATES_APPROVED: RetentionPolicy(
        RetentionCategory.TEMPLATES_APPROVED, RetentionMode.GOVERNANCE, None, False,
        "Внутренний регламент ГЭТ (бланки как эталоны, хранятся постоянно)",
    ),
    RetentionCategory.ARCHIVAL_SCANS: RetentionPolicy(
        RetentionCategory.ARCHIVAL_SCANS, RetentionMode.COMPLIANCE, None, False,
        "Исторический фонд",
    ),
    RetentionCategory.DSP: RetentionPolicy(
        RetentionCategory.DSP, RetentionMode.COMPLIANCE, 30, True,
        "ФЗ-149 «Об информации», внутренний ЛНА о ДСП (до рассекречивания + 30 лет)",
    ),
}

# ст. 22.1 ФЗ-125: для приказов по личному составу, оформленных ДО 2003
# года, срок 75 лет вместо 50 — редкий на практике случай (документы
# создаются в АИС здесь и сейчас, не переносятся из бумажного архива
# массово), но обработан явно, а не проигнорирован.
_PERSONNEL_ORDER_CUTOFF_YEAR = 2003
_PERSONNEL_ORDER_PERIOD_BEFORE_CUTOFF = 75


def _add_years(value: datetime.date, years: int) -> datetime.date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        # 29 февраля, целевой год невисокосный — ближайшая корректная дата.
        return value.replace(month=2, day=28, year=value.year + years)


def resolve_retention_until(
    category: str, *, reg_date: datetime.date, declassification_date: datetime.date | None = None,
) -> datetime.date | None:
    """None = бессрочно (Compliance/Governance без даты снятия — постоянное хранение).

    Для ДСП (conditional_on_declassification=True) без declassification_date
    возвращает None — не потому что «бессрочно», а потому что срок
    физически не может быть посчитан до рассекречивания; вызывающий код
    должен явно различать эти два случая через RETENTION_MATRIX[category].
    """
    policy = RETENTION_MATRIX[category]

    if policy.conditional_on_declassification:
        if declassification_date is None:
            return None
        return _add_years(declassification_date, policy.period_years)

    if policy.period_years is None:
        return None

    period_years = policy.period_years
    if category == RetentionCategory.ORDERS_PERSONNEL and reg_date.year < _PERSONNEL_ORDER_CUTOFF_YEAR:
        period_years = _PERSONNEL_ORDER_PERIOD_BEFORE_CUTOFF

    return _add_years(reg_date, period_years)


def is_retention_still_binding(retention_until: datetime.date | None, *, as_of: datetime.date | None = None) -> bool:
    """Единственное место, где retention_until полагается сравнивать с
    датой — весь остальной код должен звать эту функцию, а не писать
    `if retention_until and retention_until < today` напрямую.

    retention_until=None означает ОДНО из двух: «постоянно» (ORDERS_CORE,
    TEMPLATES_APPROVED, ARCHIVAL_SCANS) или «срок ещё не может быть
    посчитан» (ДСП без даты рассекречивания) — в обоих случаях документ
    остаётся под блокировкой. Голая проверка `if retention_until and ...`
    трактует NULL как falsy и тем самым как «ограничений нет», что для
    Object Locking означает практически «можно снимать когда угодно» —
    ровно обратное задуманному. Эта функция всегда возвращает True для
    None, чтобы такую ошибку нельзя было допустить по недосмотру."""
    if retention_until is None:
        return True
    if as_of is None:
        as_of = datetime.date.today()
    return retention_until >= as_of


def is_expired_at_intake(retention_until: datetime.date | None, *, as_of: datetime.date | None = None) -> bool:
    """True, если вычисленный retention_until уже в прошлом на момент
    первого сохранения карточки — ожидаемая ситуация при обратной загрузке
    старых документов с коротким сроком хранения. Сам по себе такой случай
    не ошибка данных, но требует явного решения Контролёра/Юриста: новый
    S3-объект нельзя создавать сразу без защиты только потому, что формальная
    дата уже прошла. Поэтому NormativeDocument.save() пишет предупреждение в
    WORM-аудит, а apps.core.staged_files fail-safe ставит Legal Hold до
    отдельного административного решения."""
    if retention_until is None:
        return False
    if as_of is None:
        as_of = datetime.date.today()
    return retention_until < as_of


def object_lock_params_for(policy: RetentionPolicy, retention_until: datetime.date | None) -> dict:
    """Перевести юридическую политику в параметры S3 Object Lock.

    Эти значения реально используются `apps.core.staged_files` при server-side
    copy из mutable staging в `originals`. Для фиксированного срока передаются
    `ObjectLockMode` + `ObjectLockRetainUntilDate`; для постоянного или пока
    невычислимого срока — `LegalHold=ON` без произвольной «очень далёкой» даты.

    Отдельный safety case — уже истёкшая на момент intake дата. Эта чистая
    функция сохраняет юридически вычисленную дату как есть; promotion layer,
    который знает текущую дату и выполняет S3 write, заменяет past timestamp
    на Legal Hold, чтобы только что загруженный исторический объект не оказался
    немедленно удаляемым.
    """
    if retention_until is None:
        return {
            "ObjectLockMode": None,
            "ObjectLockLegalHoldStatus": "ON",
            "ObjectLockRetainUntilDate": None,
        }
    return {
        "ObjectLockMode": "GOVERNANCE" if policy.mode == RetentionMode.GOVERNANCE else "COMPLIANCE",
        "ObjectLockLegalHoldStatus": "OFF",
        "ObjectLockRetainUntilDate": retention_until.isoformat(),
    }


def suggest_retention_category(*, access_level: str, reg_date: datetime.date) -> str | None:
    """Подсказка значения по умолчанию в форме создания — не решение за
    пользователя (см. docstring модуля). ДСП и архивный фонд однозначно
    следуют из уже существующих полей карточки; остальные категории
    требуют классификации человеком."""
    if access_level == "restricted":
        return RetentionCategory.DSP
    if reg_date.year < 2017:
        return RetentionCategory.ARCHIVAL_SCANS
    return None
