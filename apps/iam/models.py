import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.validators import MinLengthValidator
from django.db import models

from apps.core.models import TimeStampedModel, UUIDPKModel

from .managers import UserManager


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
    """Внутренний IAM без Active Directory / LDAP (ТЗ 4.6). Вход — по табельному номеру."""

    class Role(models.TextChoices):
        READER = "reader", "Читатель"
        CURATOR = "curator", "Куратор службы"
        CONTROLLER_LAWYER = "controller_lawyer", "Контролёр / Юрист"
        SECURITY_OFFICER = "security_officer", "Офицер ИБ"
        ADMINISTRATOR = "administrator", "Администратор"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    personnel_number = models.CharField(
        max_length=32, unique=True, validators=[MinLengthValidator(1)], verbose_name="Табельный номер"
    )
    full_name = models.CharField(max_length=255, verbose_name="ФИО")
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="users", verbose_name="Подразделение"
    )
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.READER)

    totp_enabled = models.BooleanField(default=False, verbose_name="2FA (TOTP) включена")

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "personnel_number"
    REQUIRED_FIELDS = ["full_name", "department"]

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self):
        return f"{self.full_name} ({self.personnel_number})"

    @property
    def requires_totp(self):
        """Роли, для которых TOTP обязателен (ТЗ 4.7)."""
        return self.role in {self.Role.ADMINISTRATOR, self.Role.CONTROLLER_LAWYER, self.Role.CURATOR}
