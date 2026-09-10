from django.core.management.base import BaseCommand

from apps.search_ocr.indexing import rebuild_all_document_search_indexes


class Command(BaseCommand):
    help = "Полностью перестроить read-model Smart Search."

    def handle(self, *args, **options):
        count = rebuild_all_document_search_indexes()
        self.stdout.write(self.style.SUCCESS(f"Переиндексировано документов: {count}."))
