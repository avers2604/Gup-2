from django.contrib import admin
from django.contrib import messages

from .models import ThesaurusEntry, ThesaurusStatus


@admin.register(ThesaurusEntry)
class ThesaurusEntryAdmin(admin.ModelAdmin):
    # draft -> verified — ручное действие куратора в админке (TH-07: не
    # проверяется на уровне модели, кто именно нажимает кнопку — см.
    # docstring ThesaurusEntry; реальное ограничение по ролям — Этап 2).
    list_display = ("canonical", "category", "service", "status", "ambiguous", "weight")
    list_filter = ("category", "service", "status", "ambiguous")
    search_fields = ("id", "canonical", "short_forms", "synonyms")
    actions = ["mark_verified", "mark_rejected"]

    @staticmethod
    def _can_curate(request):
        return request.user.is_superuser or request.user.role in {
            request.user.Role.CURATOR,
            request.user.Role.ADMINISTRATOR,
        }

    @admin.action(description="Пометить как «Подтверждено куратором»")
    def mark_verified(self, request, queryset):
        if not self._can_curate(request):
            self.message_user(request, "Недостаточно прав куратора.", messages.ERROR)
            return
        for entry in queryset:
            entry.status = ThesaurusStatus.VERIFIED
            entry.save(update_fields={"status"})

    @admin.action(description="Пометить как «Отклонено»")
    def mark_rejected(self, request, queryset):
        if not self._can_curate(request):
            self.message_user(request, "Недостаточно прав куратора.", messages.ERROR)
            return
        for entry in queryset:
            entry.status = ThesaurusStatus.REJECTED
            entry.save(update_fields={"status"})
