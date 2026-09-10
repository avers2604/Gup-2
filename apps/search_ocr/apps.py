from django.apps import AppConfig


class SearchOcrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.search_ocr"
    verbose_name = "Поиск и OCR (Smart Search)"

    def ready(self):
        # Search read-model lives in a separate module to keep the thesaurus
        # model file focused; importing it here registers the model in the app.
        from . import search_index, signals  # noqa: F401
