from django.contrib import admin
from django.core.exceptions import PermissionDenied

from . import permissions
from .models import DocumentRelation, DocumentStatusHistory, NormativeDocument, Tag


class DocumentRelationInline(admin.TabularInline):
    model = DocumentRelation
    fk_name = "from_document"
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DocumentStatusHistoryInline(admin.TabularInline):
    model = DocumentStatusHistory
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(NormativeDocument)
class NormativeDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "reg_number", "title", "doc_type", "status", "access_level", "issuer_dept", "effective_date",
        "retention_category", "retention_mode", "retention_until",
    )
    list_filter = ("status", "doc_type", "access_level", "issuer_dept", "retention_category", "retention_mode")
    search_fields = ("reg_number", "title", "summary")
    filter_horizontal = ("applied_depts", "category_tags")
    # Status transitions are legal/business events and must go through the
    # application service/Web flow where transition rules, grounds and WORM
    # audit are enforced. Admin may edit draft content, not change status.
    readonly_fields = ("status", "retention_mode", "retention_until")
    list_select_related = ("issuer_dept",)
    inlines = [DocumentRelationInline, DocumentStatusHistoryInline]

    def get_queryset(self, request):
        # Те же правила, что и в Web GUI рабочих мест (ТЗ 4.1) —
        # apps/documents/permissions.py единственный их источник.
        return permissions.visible_documents(request.user, super().get_queryset(request))

    def _can_access(self, request, obj):
        return obj is None or permissions.can_view_document(request.user, obj)

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) and self._can_access(request, obj)

    def has_add_permission(self, request):
        return (
            super().has_add_permission(request)
            and permissions.can_edit_document(request.user)
        )

    def has_change_permission(self, request, obj=None):
        return (
            super().has_change_permission(request, obj)
            and permissions.can_edit_document(request.user, obj)
        )

    def has_delete_permission(self, request, obj=None):
        # A registered NРД is never physically deleted through Django admin.
        # Lifecycle/status changes are explicit audited domain transitions.
        return False

    def save_model(self, request, obj, form, change):
        allowed = permissions.can_edit_document(request.user, obj if change else None)
        if not allowed:
            raise PermissionDenied(
                "Изменение этой карточки через административный интерфейс запрещено."
            )
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
    list_select_related = ("from_document", "to_document")
