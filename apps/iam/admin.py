from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from . import services
from .models import Department, User


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "level", "parent")
    list_filter = ("level",)
    search_fields = ("name",)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    model = User
    ordering = ("personnel_number",)
    list_display = (
        "personnel_number", "full_name", "position", "department", "role", "status", "dsp_access", "totp_enabled",
    )
    list_filter = ("role", "status", "department", "dsp_access")
    search_fields = ("personnel_number", "last_name", "first_name", "middle_name")
    readonly_fields = ("full_name", "totp_enabled")
    fieldsets = (
        (None, {"fields": ("personnel_number", "password")}),
        ("Персональные данные", {
            "fields": ("last_name", "first_name", "middle_name", "position", "email", "department", "role"),
        }),
        ("Доступ", {"fields": ("status", "dsp_access", "totp_enabled")}),
        ("Права Django", {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": (
                "personnel_number", "last_name", "first_name", "middle_name", "position",
                "department", "role", "password1", "password2",
            ),
        }),
    )
    actions = ["reset_totp"]

    @admin.action(description="Сбросить 2FA (TOTP) — потребуется повторное подключение")
    def reset_totp(self, request, queryset):
        # По одному вызову services.reset_totp() на пользователя — не
        # queryset.update(), иначе аудит (AuditLog.EventType.USER_TOTP_RESET)
        # писался бы не для каждой сброшенной учётки, а сами секреты
        # (свойство totp_secret, шифрование) нельзя корректно очистить
        # через queryset.update() — это не поле модели.
        count = 0
        for user in queryset:
            services.reset_totp(user, actor=request.user)
            count += 1
        self.message_user(request, f"2FA сброшена: {count}.")

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            fields += ("is_staff", "is_superuser", "groups", "user_permissions")
        return fields

    def save_model(self, request, obj, form, change):
        # Транзитный атрибут (не поле модели) — User.save() читает его,
        # чтобы записать оператора в комплексный аудит смены роли
        # (усиление аудита, решение Заказчика). save() сам по себе не
        # видит HTTP-запрос/текущего администратора.
        obj._audit_actor = request.user
        super().save_model(request, obj, form, change)
