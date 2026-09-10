from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.iam.async_import import enqueue_personnel_import
from apps.iam.services import import_personnel, write_report_csv


class Command(BaseCommand):
    help = "Пакетный импорт персонала из Excel (синхронно или через Celery)."

    def add_arguments(self, parser):
        parser.add_argument("file", type=str, help="Путь к .xlsx файлу")
        parser.add_argument(
            "--report-dir",
            type=str,
            default="import_reports",
            help="Куда сохранить success.csv/updated.csv/errors.csv в синхронном режиме",
        )
        parser.add_argument(
            "--actor",
            type=str,
            default=None,
            help="Табельный номер оператора, запустившего импорт",
        )
        parser.add_argument(
            "--async",
            dest="async_mode",
            action="store_true",
            help="Положить импорт в Celery и сразу вернуть task id",
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
                raise CommandError(
                    f"Оператор с табельным номером {options['actor']!r} не найден."
                )

        if options["async_mode"]:
            with file_path.open("rb") as stream:
                task_id = enqueue_personnel_import(
                    stream,
                    actor=actor,
                    original_name=file_path.name,
                )
            self.stdout.write(self.style.SUCCESS(
                f"Импорт поставлен в очередь. Celery task id: {task_id}"
            ))
            return

        with file_path.open("rb") as stream:
            report = import_personnel(stream, actor=actor)

        paths = write_report_csv(report, Path(options["report_dir"]))
        self.stdout.write(self.style.SUCCESS(
            f"Импорт завершён: {len(report.successes)} новых, "
            f"{len(report.updates)} обновлено, {len(report.errors)} с ошибками "
            f"(всего строк: {report.total})."
        ))
        for name, path in paths.items():
            self.stdout.write(f"  {name}: {path}")
        if report.errors:
            self.stdout.write(self.style.WARNING(
                f"Есть {len(report.errors)} отклонённых строк — подробности в errors.csv."
            ))
