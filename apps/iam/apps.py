from django.apps import AppConfig


class IamConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.iam"
    verbose_name = "Учётные записи и оргструктура"

    def ready(self):
        from apps.core.domain_events import require_handlers

        from . import handlers, schema  # noqa: F401
        from .services import ip_lockout_max_attempts

        require_handlers(
            "user.blocked",
            "user.password.changed",
        )
        # Security threshold is operational configuration, not a best-effort
        # hint. Reject invalid values during application startup rather than
        # discovering them only on the first authentication request.
        ip_lockout_max_attempts()
