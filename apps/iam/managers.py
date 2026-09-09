from django.contrib.auth.base_user import BaseUserManager


class UserManager(BaseUserManager):
    """Пользователи создаются по табельному номеру — почта/логин не используются (ТЗ 4.6, без AD/LDAP)."""

    use_in_migrations = True

    def _create_user(self, personnel_number, password, **extra_fields):
        if not personnel_number:
            raise ValueError("Табельный номер обязателен")
        user = self.model(personnel_number=personnel_number, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, personnel_number, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(personnel_number, password, **extra_fields)

    def create_superuser(self, personnel_number, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "administrator")
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Суперпользователь должен иметь is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Суперпользователь должен иметь is_superuser=True")
        return self._create_user(personnel_number, password, **extra_fields)
