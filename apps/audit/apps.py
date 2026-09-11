from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    verbose_name = "Аудит"

    def ready(self):
        from apps.core.domain_events import require_handlers

        from . import handlers  # noqa: F401

        require_handlers(
            "user.role.changed",
            "user.role.elevated",
            "auth.login.failed",
            "auth.session.login",
            "auth.session.logout",
            "auth.totp.reset",
        )
