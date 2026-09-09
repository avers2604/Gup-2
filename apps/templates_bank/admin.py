from django.contrib import admin

from .models import Template, TemplateFamily


class TemplateInline(admin.TabularInline):
    model = Template
    extra = 0
    fields = ("version", "change_type", "status", "download_count", "last_reviewed_at")


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
