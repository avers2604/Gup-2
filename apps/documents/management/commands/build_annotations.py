"""Досборка аннотаций для документов, распознанных до появления сборщика.

Конвейер OCR собирает аннотацию при каждом прогоне, но документы,
распознанные раньше, остались без неё: перезапускать ради этого весь OCR
(rerun_ocr) — это часы работы Tesseract на всей базе, тогда как текст
скана уже лежит в ocr_body и разбирать нужно только его.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.documents.annotation import build_annotation
from apps.documents.models import NormativeDocument


class Command(BaseCommand):
    help = "Собрать аннотации из уже распознанного текста (ocr_body)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показать, что изменится, ничего не записывая.",
        )
        parser.add_argument(
            "--rebuild",
            action="store_true",
            help=(
                "Пересобрать и те аннотации, что уже собраны автоматически. "
                "Написанные человеком не трогаются никогда."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        rebuild = options["rebuild"]

        # Аннотация, написанная человеком, не перезаписывается ни при
        # каких флагах — это его работа, и восстановить её будет неоткуда.
        candidates = NormativeDocument.objects.exclude(ocr_body="")
        if rebuild:
            candidates = candidates.filter(summary_is_auto=True) | candidates.filter(summary="")
        else:
            candidates = candidates.filter(summary="")

        built = skipped = 0
        for document in candidates.iterator(chunk_size=200):
            annotation = build_annotation(document.ocr_body)
            if not annotation:
                skipped += 1
                continue

            built += 1
            if dry_run:
                self.stdout.write(f"{document.reg_number}: {annotation[:100]}…")
                continue

            with transaction.atomic():
                locked = NormativeDocument.objects.select_for_update().get(pk=document.pk)
                # Перечитываем под блокировкой: между выборкой и записью
                # методист мог сохранить свою аннотацию, и затирать её
                # нельзя — та же проверка, что в конвейере.
                if locked.summary and not locked.summary_is_auto:
                    skipped += 1
                    built -= 1
                    continue
                locked.summary = annotation
                locked.summary_is_auto = True
                locked.save(update_fields=["summary", "summary_is_auto"])

        verb = "будет собрано" if dry_run else "собрано"
        self.stdout.write(self.style.SUCCESS(f"{verb}: {built}, пропущено: {skipped}"))
