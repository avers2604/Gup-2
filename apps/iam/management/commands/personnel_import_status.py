import json

from celery.result import AsyncResult
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Показать состояние асинхронного импорта персонала по Celery task id."

    def add_arguments(self, parser):
        parser.add_argument("task_id", type=str)

    def handle(self, *args, **options):
        result = AsyncResult(options["task_id"])
        self.stdout.write(f"state: {result.state}")
        if result.successful():
            self.stdout.write(json.dumps(result.result, ensure_ascii=False, indent=2))
        elif result.failed():
            self.stdout.write(self.style.ERROR(str(result.result)))
