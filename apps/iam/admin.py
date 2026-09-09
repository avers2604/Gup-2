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
    list_display = ("personnel_number", "full_name", "department", "role", "is_active", "totp_enabled")
    list_filter = ("role", "department", "is_active")
    search_fields = ("personnel_number", "full_name")
    fieldsets = (
        (None, {"fields": ("personnel_number", "password")}),
        ("Персональные данные", {"fields": ("full_name", "department", "role")}),
        ("Безопасность", {"fields": ("totp_enabled",)}),
        ("Права доступа", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("personnel_number", "full_name", "department", "role", "password1", "password2"),
        }),
    )
