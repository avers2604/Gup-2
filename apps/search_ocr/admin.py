from django.contrib import admin

from .models import ThesaurusEntry


@admin.register(ThesaurusEntry)
class ThesaurusEntryAdmin(admin.ModelAdmin):
    # draft -> verified — ручное действие куратора в админке (TH-07: не
    # проверяется на уровне модели, кто именно нажимает кнопку — см.
    # docstring ThesaurusEntry; реальное ограничение по ролям — Этап 2).
    list_display = ("canonical", "category", "service", "status", "ambiguous", "weight")
    list_filter = ("category", "service", "status", "ambiguous")
    search_fields = ("id", "canonical", "short_forms", "synonyms")
    actions = ["mark_verified", "mark_rejected"]

    @admin.action(description="Пометить как «Подтверждено куратором»")
    def mark_verified(self, request, queryset):
        queryset.update(status="verified")

    @admin.action(description="Пометить как «Отклонено»")
    def mark_rejected(self, request, queryset):
        queryset.update(status="rejected")
