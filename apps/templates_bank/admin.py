from django.contrib import admin
from django.core.exceptions import PermissionDenied

from . import permissions
from .models import Template, TemplateFamily


class TemplateInline(admin.TabularInline):
    model = Template
    extra = 0
    fields = ("version", "change_type", "status", "download_count", "last_reviewed_at")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TemplateFamily)
class TemplateFamilyAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    inlines = [TemplateInline]


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = ("family", "version", "change_type", "status", "download_count", "last_reviewed_at")
    list_filter = ("status", "change_type")
    search_fields = ("family__name", "version")

    @staticmethod
    def _can_manage(request):
        # Те же правила, что и в рабочем месте банка бланков (ТЗ 4.3) —
        # apps/templates_bank/permissions.py единственный их источник.
        return permissions.can_manage_templates(request.user)

    def has_add_permission(self, request):
        return super().has_add_permission(request) and self._can_manage(request)

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            return False
        return super().has_change_permission(request, obj) and self._can_manage(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if change or not self._can_manage(request):
            raise PermissionDenied("Опубликованную версию шаблона нельзя изменять.")
        # Транзитный атрибут (не поле модели) — Template.save() читает его
        # для комплексного аудита смены статуса (усиление аудита, решение
        # Заказчика). На создании (единственный путь сюда — change=False)
        # смены статуса ещё нет и писать нечего, но выставляем заранее —
        # на будущее, когда появится механизм редактирования/отката.
        obj._audit_actor = request.user
        super().save_model(request, obj, form, change)
