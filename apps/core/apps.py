from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Ядро"

    def ready(self):
        # Register pre/post-save hooks that stage immutable uploads in the
        # mutable working bucket and promote them into Object-Locked originals
        # only after the surrounding database transaction commits.
        from . import staged_files  # noqa: F401
