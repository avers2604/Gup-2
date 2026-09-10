from django.apps import AppConfig


class IamConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.iam"
    verbose_name = "Учётные записи и оргструктура"

    def ready(self):
        from . import handlers, schema  # noqa: F401
