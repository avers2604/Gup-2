from django.apps import AppConfig


class SearchOcrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.search_ocr"
    verbose_name = "Поиск и OCR (Smart Search)"

    def ready(self):
        # The read-model lives outside models.py to keep thesaurus/write concerns
        # separate, but must be imported during app startup for model registry and
        # migrations. Signals schedule index refreshes only after DB commit.
        from . import read_models  # noqa: F401
        from . import signals  # noqa: F401
