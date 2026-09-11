from django.core.management.base import BaseCommand

from apps.core.outbox import enqueue
from apps.documents.models import NormativeDocument


class Command(BaseCommand):
    help = (
        "Ретроактивная постановка конвейера OCR (Этап 3) в очередь для "
        "документов, у которых нет файла оригинала на момент появления "
        "конвейера, или для повторного распознавания уже обработанных "
        "документов (--force, например после смены движка/порогов)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=100,
            help="Максимальное число документов для постановки в очередь (по умолчанию 100)",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Перераспознать даже документы, уже прошедшие OCR (не только NOT_PROCESSED)",
        )

    def handle(self, *args, **options):
        queryset = NormativeDocument.objects.exclude(files_original="")

        if not options["force"]:
            queryset = queryset.filter(ocr_status=NormativeDocument.OcrStatus.NOT_PROCESSED)

        queryset = queryset.order_by("reg_date")[: options["limit"]]

        count = 0
        for document in queryset:
            # Тот же durable delivery boundary, что используется при штатной
            # загрузке/замене оригинала в NormativeDocument.save(). Прямой
            # Celery .delay() теряет ручной rerun, если Redis недоступен в
            # момент запуска команды; TaskOutbox сохраняет intent в БД и
            # повторит доставку через dispatch_pending().
            enqueue("apps.documents.tasks.run_ocr_for_document", [str(document.pk)])
            count += 1

        self.stdout.write(self.style.SUCCESS(f"Поставлено в очередь: {count}"))
