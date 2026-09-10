from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

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
    readonly_fields = ("full_name",)
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
