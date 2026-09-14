from django.contrib import admin

from . import permissions
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "actor_personnel_number", "object_type", "object_id")
    list_filter = ("event_type",)
    search_fields = ("actor_personnel_number", "object_id")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_module_permission(self, request):
        return (
            super().has_module_permission(request)
            and permissions.can_view_audit_log(request.user)
        )

    def has_view_permission(self, request, obj=None):
        return (
            super().has_view_permission(request, obj)
            and permissions.can_view_audit_log(request.user)
        )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if permissions.can_view_audit_log(request.user):
            return queryset
        return queryset.none()

    def has_add_permission(self, request):
        # AuditLog entries are emitted only by audited application events.
        # A manual admin INSERT would be an unaudited fabrication of evidence.
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
