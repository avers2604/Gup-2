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
