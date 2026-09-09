import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models

from apps.core.models import TimeStampedModel, UUIDPKModel

from .managers import UserManager
from .validators import cyrillic_name_validator, personnel_number_validator


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
        READER = "reader", "Читатель"
        CURATOR = "curator", "Куратор службы"
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

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "personnel_number"
    REQUIRED_FIELDS = ["last_name", "first_name", "position", "department"]

    # Порядок privilege-уровней ролей — дословно порядок перечисления в ТЗ
    # 4.6 («Читатель … Куратор … Контролёр/Юрист … Офицер ИБ …
    # Администратор»). Используется для определения «повышения роли» при
    # импорте персонала — не придуман отдельно, только формализован.
    ROLE_PRIVILEGE_ORDER = [
        Role.READER, Role.CURATOR, Role.CONTROLLER_LAWYER, Role.SECURITY_OFFICER, Role.ADMINISTRATOR,
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
    def requires_totp(self):
        """Роли, для которых TOTP обязателен (ТЗ 4.7)."""
        return self.role in {self.Role.ADMINISTRATOR, self.Role.CONTROLLER_LAWYER, self.Role.CURATOR}

    def role_rank(self):
        try:
            return self.ROLE_PRIVILEGE_ORDER.index(self.role)
        except ValueError:
            return -1

    def save(self, *args, **kwargs):
        # status — источник истины для жизненного цикла учётной записи;
        # is_active синхронизируется от него, а не задаётся отдельно, чтобы
        # два поля не могли разъехаться (is_active нужен Django-аутентификации
        # как есть — под него нельзя просто подставить свойство).
        self.is_active = self.status != self.Status.BLOCKED
        super().save(*args, **kwargs)
