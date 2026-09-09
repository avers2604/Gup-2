from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.iam.services import import_personnel, write_report_csv


class Command(BaseCommand):
    help = (
        "Пакетный импорт персонала из Excel (ТЗ 4.6). Временный CLI-вход "
        "до появления рабочего места администратора (Этап 2+) — команда "
        "делает то же самое, что будет делать веб-форма загрузки."
    )

    def add_arguments(self, parser):
        parser.add_argument("file", type=str, help="Путь к .xlsx файлу")
        parser.add_argument(
            "--report-dir", type=str, default="import_reports",
            help="Куда сохранить success.csv/updated.csv/errors.csv (по умолчанию ./import_reports)",
        )
        parser.add_argument(
            "--actor", type=str, default=None,
            help="Табельный номер оператора, запустившего импорт (для аудита повышения роли)",
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

        with file_path.open("rb") as f:
            report = import_personnel(f, actor=actor)

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
