from django.contrib import admin
from django.contrib import messages

from apps.audit.models import AuditLog

from .models import ThesaurusEntry, ThesaurusStatus


@admin.register(ThesaurusEntry)
class ThesaurusEntryAdmin(admin.ModelAdmin):
    # draft -> verified/rejected — ручное действие куратора в админке.
    # TH-07 частично реализовано: роль проверяется (_can_curate — Куратор
    # или Администратор), действие пишется в WORM-аудит с оператором и
    # diff (ниже). НЕ проверяется — что подтверждающий Куратор относится
    # именно к службе-владельцу записи (`ThesaurusEntry.service`): любой
    # Куратор сейчас может верифицировать термины любой службы, не только
    # своей. Это сознательно не решено самостоятельно (см. STACK.md,
    # раздел про открытые пробелы тезауруса) — сужение до «только куратор
    # своей службы» требует решения, откуда админка узнаёт связку
    # «Куратор -> служба, которой он куратор» (её сейчас в модели User
    # нет вообще), это отдельная задача, не однострочная правка.
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

    def _apply_status(self, request, queryset, new_status, action_label):
        if not self._can_curate(request):
            self.message_user(request, "Недостаточно прав куратора.", messages.ERROR)
            return
        changes = {}
        for entry in queryset:
            if entry.status == new_status:
                continue
            changes[entry.pk] = [entry.status, new_status]
            entry.status = new_status
            entry.save(update_fields={"status"})
        if changes:
            # Один общий аудит-запись на действие (список затронутых id +
            # diff), тот же принцип, что у THESAURUS_UPDATED при импорте
            # (apps/search_ocr/services.py) — не по записи на каждую
            # строку, иначе массовое действие даёт шум в журнале.
            AuditLog.objects.create(
                event_type=AuditLog.EventType.THESAURUS_UPDATED,
                actor=request.user if request.user.is_authenticated else None,
                actor_personnel_number=getattr(request.user, "personnel_number", ""),
                object_type="ThesaurusEntry",
                object_id="admin_status_change",
                details={"action": action_label, "changes": changes},
            )

    @admin.action(description="Пометить как «Подтверждено куратором»")
    def mark_verified(self, request, queryset):
        self._apply_status(request, queryset, ThesaurusStatus.VERIFIED, "mark_verified")

    @admin.action(description="Пометить как «Отклонено»")
    def mark_rejected(self, request, queryset):
        self._apply_status(request, queryset, ThesaurusStatus.REJECTED, "mark_rejected")
