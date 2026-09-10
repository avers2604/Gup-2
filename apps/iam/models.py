import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models, transaction
from django.utils import timezone

from apps.core.domain_events import publish
from apps.core.models import TimeStampedModel, UUIDPKModel

from . import totp_crypto
from .managers import UserManager
from .validators import cyrillic_name_validator, personnel_number_validator

# Срок действия пароля (решение Заказчика: усиление аудита/парольной
# политики) — 365 дней с момента последней смены.
PASSWORD_EXPIRY_DAYS = 365
# Глубина истории паролей, которую нельзя повторно использовать (решение
# Заказчика) — см. PasswordHistoryEntry и apps.iam.validators.PasswordHistoryValidator.
PASSWORD_HISTORY_DEPTH = 10


class Department(UUIDPKModel, TimeStampedModel):
    """4-уровневое дерево оргструктуры ГЭТ (ТЗ 4.6):
    Аппарат управления → Службы → Парки/Районы → Линейные участки/подстанции."""

    class Level(models.IntegerChoices):
        HEAD_OFFICE = 1, "Аппарат управления"
        SERVICE = 2, "Служба"
        DEPOT = 3, "Парк / Район"
        SITE = 4, "Линейный участок / подстанция"

    name = models.CharField(max_length=255)
    level = models.PositiveSmallIntegerField(choices=Level.choices)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )

    class Meta:
        verbose_name = "Подразделение"
        verbose_name_plural = "Подразделения"
        ordering = ["level", "name"]
        constraints = [
            # Неоднозначность путей department_path при импорте персонала
            # (apps.iam.services._resolve_department резолвит по
            # name+parent): пока есть только уровни 1-2 (реальные службы
            # из ТЗ), дублей нет, но при появлении уровня 3-4 («Нарядная»,
            # «Диспетчерская станция» в разных парках) без этого
            # ограничения ничего не мешает завести два одноимённых узла
            # под одним родителем. Добавлено сейчас, не отложено до
            # появления данных: NULL parent (уровень 1, Аппарат
            # управления) этим ограничением НЕ защищён — в Postgres NULL
            # не равен NULL, повторный Аппарат управления с parent=NULL
            # такой констрейнт не поймает (на практике он один, отдельным
            # правилом сейчас не покрыто).
            models.UniqueConstraint(fields=["name", "parent"], name="unique_department_name_per_parent"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.level == self.Level.HEAD_OFFICE and self.parent is not None:
            raise ValidationError("Аппарат управления не может иметь родителя.")
        if self.level != self.Level.HEAD_OFFICE and self.parent is None:
            raise ValidationError("Для уровня ниже «Аппарат управления» родитель обязателен.")
        if self.parent is not None and self.parent.level != self.level - 1:
            raise ValidationError("Родитель должен быть на уровень выше дочернего подразделения.")


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    """Внутренний IAM без Active Directory / LDAP (ТЗ 4.6). Вход — по табельному номеру.

    Поля last_name/first_name/middle_name/position/email/dsp_access/status
    — под формат образца файла пакетного импорта персонала (ТЗ 4.6.2), а
    не придуманы отдельно от него."""

    class Role(models.TextChoices):
        # «Куратор службы» упразднён отдельным решением Заказчика (ТЗ-БЗ-ГЭТ-
        # 2026-V2.2, доп. решение — не отдельная ревизия ТЗ, а прямое указание
        # в рамках этой сессии): весь функционал роли передан на роль выше по
        # ROLE_PRIVILEGE_ORDER — CONTROLLER_LAWYER (см. миграцию
        # 0006_remove_curator_add_methodist, переносящую существующих
        # пользователей с role=curator, и STACK.md → раздел про упразднение
        # роли). Ни в одном месте кода роль CURATOR намеренно не оставлена —
        # вырезана полностью, а не помечена deprecated.
        READER = "reader", "Читатель"
        # «Методист подразделения» — новая роль (п.8.1 решения Заказчика,
        # «для Б1»). Приложение Б1 самого ТЗ сюда не передано, поэтому явный
        # численный уровень привилегий роли не специфицирован документом —
        # размещение в ROLE_PRIVILEGE_ORDER сразу после READER (вместо
        # упразднённого CURATOR) это самостоятельное, а не вычитанное из ТЗ
        # решение; см. открытый вопрос в STACK.md.
        METHODIST = "methodist", "Методист подразделения"
        CONTROLLER_LAWYER = "controller_lawyer", "Контролёр / Юрист"
        SECURITY_OFFICER = "security_officer", "Офицер ИБ"
        ADMINISTRATOR = "administrator", "Администратор"

    class Status(models.TextChoices):
        ACTIVE = "active", "Активен"
        BLOCKED = "blocked", "Заблокирован"
        PASSWORD_CHANGE_REQUIRED = "password_change_required", "Требуется смена пароля"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    personnel_number = models.CharField(
        max_length=16, unique=True, validators=[personnel_number_validator],
        verbose_name="Табельный номер",
    )
    last_name = models.CharField(max_length=100, validators=[cyrillic_name_validator], verbose_name="Фамилия")
    first_name = models.CharField(max_length=100, validators=[cyrillic_name_validator], verbose_name="Имя")
    middle_name = models.CharField(
        max_length=100, blank=True, validators=[cyrillic_name_validator], verbose_name="Отчество"
    )
    position = models.CharField(max_length=200, verbose_name="Должность")
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="users", verbose_name="Подразделение"
    )
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.READER)
    dsp_access = models.BooleanField(default=False, verbose_name="Допуск к ДСП")
    email = models.EmailField(blank=True, verbose_name="Email (для SMTP-дайджестов)")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.ACTIVE)

    totp_enabled = models.BooleanField(default=False, verbose_name="2FA (TOTP) включена")
    # base32-секрет TOTP (RFC 6238), зашифрован отдельным ключом
    # (TOTP_ENCRYPTION_KEY, apps/iam/totp_crypto.py) — усиление, добавленное
    # этой партией поверх ранее честно задокументированного пробела
    # (открытое хранение). totp_secret_plaintext — переходное поле для
    # учёток, заведённых до миграции (management-команда
    # encrypt_totp_secrets): пока в нём есть значение, свойство totp_secret
    # читает его как запасной вариант. Оба поля editable=False — доступны
    # только через свойство totp_secret ниже и apps.iam.totp/services.
    totp_secret_encrypted = models.CharField(
        max_length=255, blank=True, editable=False, verbose_name="Секрет TOTP (зашифрован)",
    )
    totp_secret_plaintext = models.CharField(
        max_length=64, blank=True, editable=False, verbose_name="Секрет TOTP (устар., открытый текст)",
    )

    # Момент последней фактической смены пароля (включая первую установку
    # при создании учётной записи) — источник для is_password_expired.
    # editable=False: выставляется только из save() по факту реального
    # изменения хэша, а не через форму. NULL — учётная запись, для которой
    # это ещё ни разу не отслеживалось (например, создана до появления
    # этого поля) — см. is_password_expired про честную границу такого случая.
    auth_version = models.PositiveIntegerField(default=0, editable=False)
    totp_last_step = models.BigIntegerField(default=-1, editable=False)
    totp_pending_secret = models.CharField(max_length=255, blank=True, editable=False)
    totp_pending_until = models.DateTimeField(null=True, blank=True, editable=False)

    password_changed_at = models.DateTimeField(
        null=True, blank=True, editable=False, verbose_name="Пароль изменён",
    )

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "personnel_number"
    REQUIRED_FIELDS = ["last_name", "first_name", "position", "department"]

    # Порядок privilege-уровней ролей — изначально дословно порядок
    # перечисления в ТЗ 4.6 («Читатель … Куратор … Контролёр/Юрист … Офицер
    # ИБ … Администратор»); CURATOR с этой сессии упразднён отдельным
    # решением Заказчика, METHODIST добавлен на его место в порядке (см.
    # Role выше — размещение METHODIST здесь НЕ из ТЗ, самостоятельное
    # решение, задокументировано в STACK.md). Используется для определения
    # «повышения роли» при импорте персонала и для комплексного аудита
    # изменений ролей (User.save()) — не придуман отдельно, только
    # формализован.
    #
    # ВАЖНО: Офицер ИБ и Контролёр/Юрист по смыслу — параллельные ветки
    # аудита (безопасность vs юридическая проверка), а не один выше
    # другого. Линейная модель делает переход между ними асимметричным
    # (в одну сторону — «повышение» с audit-записью, в другую — нет).
    # Не решено самостоятельно — см. STACK.md → «Открытый вопрос:
    # линейный порядок привилегий ролей», нужно решение Заказчика/ЧТЗ
    # до того, как такой переход реально пройдёт через импорт.
    ROLE_PRIVILEGE_ORDER = [
        Role.READER, Role.METHODIST, Role.CONTROLLER_LAWYER, Role.SECURITY_OFFICER, Role.ADMINISTRATOR,
    ]

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self):
        return f"{self.full_name} ({self.personnel_number})"

    @property
    def full_name(self):
        return " ".join(part for part in (self.last_name, self.first_name, self.middle_name) if part)

    @property
    def totp_secret(self):
        """Прозрачный доступ к секрету TOTP — вызывающий код (apps.iam.totp,
        apps.iam.services) читает/пишет user.totp_secret как обычное поле;
        шифрование/расшифровка и переходный fallback на устаревшее открытое
        поле (totp_secret_plaintext, до прогона encrypt_totp_secrets)
        скрыты здесь."""
        if self.totp_secret_encrypted:
            return totp_crypto.decrypt_totp_secret(self.totp_secret_encrypted)
        return self.totp_secret_plaintext

    @totp_secret.setter
    def totp_secret(self, value):
        self.totp_secret_encrypted = totp_crypto.encrypt_totp_secret(value) if value else ""
        self.totp_secret_plaintext = ""

    @property
    def requires_totp(self):
        """Require a second factor for administrators and Django privileged accounts."""
        return self.role == self.Role.ADMINISTRATOR or self.is_superuser or self.is_staff

    def role_rank(self):
        try:
            return self.ROLE_PRIVILEGE_ORDER.index(self.role)
        except ValueError:
            return -1

    @property
    def is_password_expired(self):
        """Password age policy enforced in session middleware and JWT permissions."""
        if self.password_changed_at is None:
            return False
        return (timezone.now() - self.password_changed_at).days >= PASSWORD_EXPIRY_DAYS

    @transaction.atomic
    def save(self, *args, **kwargs):
        """Переходы состояния учётной записи + публикация доменных событий.

        Здесь остаётся только то, что меняет СОСТОЯНИЕ самой записи
        (синхронизация is_active со status, отметка password_changed_at).
        Всё, что выходит за границу этой модели — WORM-аудит смены роли,
        история паролей, принудительный сброс сессий — вынесено в
        обработчики событий (apps/iam/handlers.py, apps/audit/handlers.py)
        и подключается через шину apps.core.domain_events. Это же снимает
        прямой импорт apps.audit из apps.iam: доменные границы (README →
        «модульный монолит (DDD), границы доменов — отдельные
        Django-приложения без прямых импортов друг в друга») соблюдаются
        через событие, а не через импорт чужой модели.

        publish() диспатчит СИНХРОННО в текущей транзакции (не
        on_commit) — запись аудита и истории паролей обязана быть
        атомарной со сменой состояния: откат транзакции должен откатывать
        и её, см. docstring apps/core/domain_events.py.
        """
        # status — источник истины для жизненного цикла учётной записи;
        # is_active синхронизируется от него, а не задаётся отдельно, чтобы
        # два поля не могли разъехаться (is_active нужен Django-аутентификации
        # как есть — под него нельзя просто подставить свойство).
        previous = (
            type(self).objects.select_for_update().filter(pk=self.pk)
            .values_list("status", "role", "password", "auth_version", flat=False)
            .first()
        )
        was_blocked = previous is not None and previous[0] == self.Status.BLOCKED
        previous_role = previous[1] if previous is not None else None
        previous_password_hash = previous[2] if previous is not None else None
        is_new = previous is None
        role_changed = not is_new and previous_role != self.role
        password_changed = is_new or previous_password_hash != self.password

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if not update_fields:
                return
            # Do not publish changes to fields that will not be persisted.
            if previous:
                if "status" not in update_fields:
                    self.status = previous[0]
                if "role" not in update_fields:
                    self.role = previous[1]
                    role_changed = False
                if "password" not in update_fields:
                    self.password = previous[2]
                    password_changed = False
            kwargs["update_fields"] = update_fields | {"is_active"}
        self.is_active = self.status != self.Status.BLOCKED
        if previous and (password_changed or self.status != previous[0] or role_changed):
            self.auth_version = previous[3] + 1
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"].add("auth_version")
        if password_changed:
            self.password_changed_at = timezone.now()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"password_changed_at"}

        super().save(*args, **kwargs)

        # Принудительный сброс сессий при блокировке (ТЗ 4.7) — is_active
        # сам по себе не выкидывает уже вошедшего пользователя, только
        # запрещает будущий вход. Срабатывает именно на ПЕРЕХОД в blocked,
        # не на каждое сохранение уже заблокированной записи.
        if self.status == self.Status.BLOCKED and not was_blocked:
            publish("user.blocked", user=self)

        # Комплексный аудит изменений ролей (решение Заказчика: «фиксировать
        # все изменения ролей — кто изменил, кому, какая роль, метка
        # времени») — любое реальное изменение role, независимо от
        # направления (не только повышение) и независимо от HTTP-контура
        # или его отсутствия (admin, будущий API управления пользователями).
        # actor берётся из необязательного транзитного атрибута _audit_actor
        # (не поле модели — не должен персистироваться), который обязан
        # выставить вызывающий код ДО save(), если знает, кто выполняет
        # изменение (см. UserAdmin.save_model, apps.iam.services.import_personnel)
        # — у save() самого по себе нет доступа к HTTP-запросу/оператору.
        # Не заменяет собой более узкое событие "user.role.elevated"
        # (apps.iam.services.import_personnel, только для импорта, только
        # повышение) — оба события могут быть записаны на одно и то же
        # изменение, это намеренное пересечение под разных потребителей
        # (комплексный аудит vs узкий сигнал повышения при импорте), см.
        # STACK.md.
        if role_changed:
            publish(
                "user.role.changed",
                user=self,
                actor=getattr(self, "_audit_actor", None),
                previous_role=previous_role,
                new_role=self.role,
            )

        # Парольная политика (решение Заказчика): история последних
        # PASSWORD_HISTORY_DEPTH паролей — нельзя использовать повторно
        # (apps.iam.validators.PasswordHistoryValidator). В историю
        # попадает именно ЗАМЕНЯЕМЫЙ (старый) хэш, а не новый — на новый
        # смотреть пока не на что, а старый в этот момент как раз
        # становится «использованным ранее» для будущих проверок. На
        # создании учётной записи (is_new) писать нечего — предыдущего
        # пароля не существовало.
        if password_changed and not is_new and previous_password_hash:
            publish(
                "user.password.changed",
                user=self,
                previous_password_hash=previous_password_hash,
            )


class PasswordHistoryEntry(UUIDPKModel):
    """Хэши ранее использованных паролей — под запрет повторного
    использования последних PASSWORD_HISTORY_DEPTH (решение Заказчика).
    Хранится только хэш (тот же алгоритм, что и User.password — Argon2id),
    не сам пароль — проверка через django.contrib.auth.hashers.check_password(),
    та же функция, что и обычная аутентификация."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_history")
    password_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Запись истории паролей"
        verbose_name_plural = "История паролей"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.personnel_number} · {self.created_at:%Y-%m-%d %H:%M}"


class UsedLoginTicket(models.Model):
    digest = models.CharField(max_length=64, primary_key=True)
    expires_at = models.DateTimeField(db_index=True)
