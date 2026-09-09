from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.search_ocr.services import import_thesaurus


class Command(BaseCommand):
    help = (
        "Импорт/обновление тезауруса Smart Search (ТЗ 4.4.1) из JSON-файла "
        "формата docs/thesaurus/thesaurus_v0.9_draft.json. Идемпотентно: "
        "upsert по id, повторный запуск с тем же файлом ничего не меняет."
    )

    def add_arguments(self, parser):
        parser.add_argument("file", type=str, help="Путь к JSON-файлу тезауруса")
        parser.add_argument(
            "--actor", type=str, default=None,
            help="Табельный номер оператора, запустившего импорт (для аудита THESAURUS_UPDATED)",
        )

    def handle(self, *args, **options):
        file_path = Path(options["file"])
        if not file_path.exists():
            raise CommandError(f"Файл не найден: {file_path}")

        actor = None
        if options["actor"]:
            from apps.iam.models import User

            actor = User.objects.filter(personnel_number=options["actor"]).first()
            if actor is None:
                raise CommandError(f"Оператор с табельным номером {options['actor']!r} не найден.")

        report = import_thesaurus(file_path, actor=actor)

        self.stdout.write(self.style.SUCCESS(
            f"Импорт тезауруса завершён: {len(report.created)} новых, "
            f"{len(report.updated)} обновлено, {len(report.unchanged)} без изменений, "
            f"{len(report.errors)} с ошибками (обработано записей: {report.total})."
        ))
        for entry_id, detail in report.errors:
            self.stdout.write(self.style.ERROR(f"  {entry_id}: {detail}"))
        for warning in report.warnings:
            self.stdout.write(self.style.WARNING(f"  {warning}"))
