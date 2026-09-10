from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import DocumentRelation, DocumentStatusHistory, NormativeDocument, Tag


class DocumentRelationInline(admin.TabularInline):
    model = DocumentRelation
    fk_name = "from_document"
    extra = 0


class DocumentStatusHistoryInline(admin.TabularInline):
    model = DocumentStatusHistory
    extra = 0


@admin.register(NormativeDocument)
class NormativeDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "reg_number", "title", "doc_type", "status", "access_level", "issuer_dept", "effective_date",
        "retention_category", "retention_mode", "retention_until",
    )
    list_filter = ("status", "doc_type", "access_level", "issuer_dept", "retention_category", "retention_mode")
    search_fields = ("reg_number", "title", "summary")
    filter_horizontal = ("applied_depts", "category_tags")
    readonly_fields = ("retention_mode", "retention_until")
    inlines = [DocumentRelationInline, DocumentStatusHistoryInline]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser or request.user.dsp_access:
            return queryset
        return queryset.filter(access_level=NormativeDocument.AccessLevel.GENERAL)

    def _can_access(self, request, obj):
        return (
            obj is None
            or obj.access_level == NormativeDocument.AccessLevel.GENERAL
            or request.user.is_superuser
            or request.user.dsp_access
        )

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) and self._can_access(request, obj)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and self._can_access(request, obj)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and self._can_access(request, obj)

    def save_model(self, request, obj, form, change):
        if not self._can_access(request, obj):
            raise PermissionDenied("Для работы с документами ДСП требуется соответствующий допуск.")
        # Транзитный атрибут (не поле модели) — NormativeDocument.save()
        # читает его для комплексного аудита смены статуса документа
        # (усиление аудита, решение Заказчика).
        obj._audit_actor = request.user
        super().save_model(request, obj, form, change)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(DocumentRelation)
class DocumentRelationAdmin(admin.ModelAdmin):
    list_display = ("from_document", "relation_type", "to_document", "created_at")
    list_filter = ("relation_type",)
