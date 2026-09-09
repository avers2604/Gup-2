from django.contrib import admin

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
    list_display = ("reg_number", "title", "doc_type", "status", "access_level", "issuer_dept", "effective_date")
    list_filter = ("status", "doc_type", "access_level", "issuer_dept")
    search_fields = ("reg_number", "title", "summary")
    filter_horizontal = ("applied_depts", "category_tags")
    inlines = [DocumentRelationInline, DocumentStatusHistoryInline]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(DocumentRelation)
class DocumentRelationAdmin(admin.ModelAdmin):
    list_display = ("from_document", "relation_type", "to_document", "created_at")
    list_filter = ("relation_type",)
