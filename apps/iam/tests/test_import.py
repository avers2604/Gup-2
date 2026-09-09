import io
import threading
from unittest import mock

import openpyxl
from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase

from apps.audit.models import AuditLog

from ..models import Department, User
from ..services import HEADER, MAX_ROWS, import_personnel, write_report_csv


def _build_xlsx(rows: list[dict]) -> io.BytesIO:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADER)
    for row in rows:
        ws.append([row.get(col, "") for col in HEADER])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _make_tree():
    # Миграция iam.0002_seed_departments уже создаёт «Аппарат управления»
    # и реальные службы из ТЗ — переиспользуем их, а не создаём дубликаты
    # (два узла с одинаковым именем и уровнем ломают резолюцию
    # department_path: filter(name=..., parent=...).first() не гарантирует,
    # какой из двух совпадающих вернёт).
    head = Department.objects.get(name="Аппарат управления", level=Department.Level.HEAD_OFFICE)
    movement = Department.objects.get(name="Служба движения", level=Department.Level.SERVICE, parent=head)
    track = Department.objects.get(name="Служба пути", level=Department.Level.SERVICE, parent=head)
    return head, movement, track


def _row(**overrides):
    base = dict(
        tab_number="0012478",
        last_name="Иванов",
        first_name="Пётр",
        middle_name="Сергеевич",
        position="Водитель трамвая 1 класса",
        department_path="Аппарат управления / Служба движения",
        role="reader",
        dsp_access="true",
        email="ivanov@get.spb.ru",
        status="",
    )
    base.update(overrides)
    return base


class PersonnelImportBasicTests(TestCase):
    def setUp(self):
        self.head, self.movement, self.track = _make_tree()

    def test_new_row_creates_user(self):
        report = import_personnel(_build_xlsx([_row()]))
        self.assertEqual(len(report.successes), 1)
        self.assertEqual(len(report.updates), 0)
        self.assertEqual(len(report.errors), 0)

        user = User.objects.get(personnel_number="0012478")
        self.assertEqual(user.last_name, "Иванов")
        self.assertEqual(user.first_name, "Пётр")
        self.assertEqual(user.department, self.movement)
        self.assertEqual(user.role, User.Role.READER)
        self.assertTrue(user.dsp_access)
        self.assertEqual(user.status, User.Status.ACTIVE)
        self.assertTrue(user.is_active)
        self.assertFalse(user.has_usable_password())

    def test_department_path_with_extra_whitespace_resolves(self):
        # Пример из образца файла — пробелы вокруг "/" (" / ").
        report = import_personnel(_build_xlsx(
            [_row(department_path=" Аппарат управления  /  Служба движения ")]
        ))
        self.assertEqual(len(report.successes), 1)
        self.assertEqual(User.objects.get(personnel_number="0012478").department, self.movement)

    def test_missing_required_column_rejected(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["tab_number", "last_name"])  # без остальных обязательных колонок
        ws.append(["0012478", "Иванов"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        with self.assertRaises(ValidationError):
            import_personnel(buf)


class PersonnelImportUpsertTests(TestCase):
    """Дубль по tab_number -> обновление существующей записи, а не новая (задание)."""

    def setUp(self):
        self.head, self.movement, self.track = _make_tree()

    def test_duplicate_tab_number_updates_existing_record(self):
        import_personnel(_build_xlsx([_row(position="Водитель трамвая 1 класса")]))
        self.assertEqual(User.objects.filter(personnel_number="0012478").count(), 1)

        report = import_personnel(_build_xlsx([_row(position="Старший водитель трамвая")]))
        self.assertEqual(len(report.successes), 0)
        self.assertEqual(len(report.updates), 1)
        self.assertEqual(User.objects.filter(personnel_number="0012478").count(), 1)
        self.assertEqual(
            User.objects.get(personnel_number="0012478").position, "Старший водитель трамвая"
        )

    def test_reimport_preserves_password(self):
        import_personnel(_build_xlsx([_row()]))
        user = User.objects.get(personnel_number="0012478")
        user.set_password("Sup3r$ecret!")
        user.save()

        import_personnel(_build_xlsx([_row(position="Другая должность")]))
        user.refresh_from_db()
        self.assertTrue(user.check_password("Sup3r$ecret!"))


class PersonnelImportDepartmentPathTests(TestCase):
    """Несуществующий department_path -> строка отклоняется, импорт остальных продолжается (задание)."""

    def setUp(self):
        self.head, self.movement, self.track = _make_tree()

    def test_unknown_department_rejects_row_but_continues_import(self):
        rows = [
            _row(tab_number="0001", department_path="Аппарат управления / Служба движения"),
            _row(tab_number="0002", department_path="Аппарат управления / Служба призраков"),
            _row(tab_number="0003", department_path="Аппарат управления / Служба пути"),
        ]
        report = import_personnel(_build_xlsx(rows))

        self.assertEqual(len(report.successes), 2)
        self.assertEqual(len(report.errors), 1)
        self.assertEqual(report.errors[0].tab_number, "0002")
        self.assertIn("Служба призраков", report.errors[0].detail)

        self.assertTrue(User.objects.filter(personnel_number="0001").exists())
        self.assertFalse(User.objects.filter(personnel_number="0002").exists())
        self.assertTrue(User.objects.filter(personnel_number="0003").exists())


class PersonnelImportRoleElevationAuditTests(TestCase):
    """Смена роли на более привилегированную -> отдельный аудит в WORM (задание)."""

    def setUp(self):
        self.head, self.movement, self.track = _make_tree()

    def test_role_elevation_creates_audit_entry(self):
        import_personnel(_build_xlsx([_row(role="reader")]))
        self.assertEqual(
            AuditLog.objects.filter(event_type=AuditLog.EventType.USER_ROLE_ELEVATED).count(), 0
        )

        import_personnel(_build_xlsx([_row(role="curator")]))
        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.USER_ROLE_ELEVATED)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().details["previous_role"], "reader")
        self.assertEqual(entries.first().details["new_role"], "curator")

    def test_role_downgrade_does_not_create_audit_entry(self):
        import_personnel(_build_xlsx([_row(role="curator")]))
        import_personnel(_build_xlsx([_row(role="reader")]))
        self.assertEqual(
            AuditLog.objects.filter(event_type=AuditLog.EventType.USER_ROLE_ELEVATED).count(), 0
        )

    def test_new_user_creation_does_not_count_as_elevation(self):
        # Совсем новый пользователь с высокой ролью — это создание, не повышение.
        import_personnel(_build_xlsx([_row(role="admin")]))
        self.assertEqual(
            AuditLog.objects.filter(event_type=AuditLog.EventType.USER_ROLE_ELEVATED).count(), 0
        )

    def test_audit_entry_is_itself_immutable(self):
        # Проверка того, что запись об аудите повышения роли подчиняется
        # тем же WORM-правилам, что и любая другая (apps.audit.tests) —
        # не дублирование тех тестов, а проверка, что именно ЭТОТ путь
        # создания записи не в обход них.
        import_personnel(_build_xlsx([_row(role="reader")]))
        import_personnel(_build_xlsx([_row(role="curator")]))
        entry = AuditLog.objects.get(event_type=AuditLog.EventType.USER_ROLE_ELEVATED)
        with self.assertRaises(PermissionError):
            entry.delete()


class PersonnelImportNoMassDeletionTests(TestCase):
    """Массовое удаление — не через импорт; только явная блокировка status=blocked (задание)."""

    def setUp(self):
        self.head, self.movement, self.track = _make_tree()

    def test_user_absent_from_file_is_left_untouched(self):
        import_personnel(_build_xlsx([_row(tab_number="0001"), _row(tab_number="0002")]))
        self.assertTrue(User.objects.filter(personnel_number="0001").exists())

        # Второй импорт содержит только 0002 — 0001 в файле нет вовсе.
        import_personnel(_build_xlsx([_row(tab_number="0002", position="Другая должность")]))

        user_1 = User.objects.get(personnel_number="0001")
        self.assertEqual(user_1.status, User.Status.ACTIVE)
        self.assertTrue(user_1.is_active)

    def test_explicit_blocked_status_deactivates_user(self):
        import_personnel(_build_xlsx([_row(tab_number="0001")]))
        self.assertTrue(User.objects.get(personnel_number="0001").is_active)

        import_personnel(_build_xlsx([_row(tab_number="0001", status="blocked")]))
        user = User.objects.get(personnel_number="0001")
        self.assertEqual(user.status, User.Status.BLOCKED)
        self.assertFalse(user.is_active)


class PersonnelImportRowLimitTests(TestCase):
    """Максимум строк за операцию: 5000 (ТЗ 4.6).

    Логика лимита («>N строк — отказ ещё до обработки») не зависит от
    конкретного числа, поэтому проверяем её на маленьком N через мок
    MAX_ROWS, а не гоняем реальные 5000 строк через БД в каждом прогоне
    тестов — тест того же самого условия, но без ~35 секунд накладных
    расходов на 5000 отдельных INSERT на каждый запуск CI."""

    def setUp(self):
        self.head, self.movement, self.track = _make_tree()

    def test_over_limit_file_rejected_entirely(self):
        with mock.patch("apps.iam.services.MAX_ROWS", 3):
            rows = [_row(tab_number=str(1000 + i)) for i in range(4)]
            with self.assertRaises(ValidationError):
                import_personnel(_build_xlsx(rows))
        # Ничего не должно было импортироваться — лимит проверяется до обработки строк.
        self.assertEqual(User.objects.count(), 0)

    def test_exactly_at_limit_is_allowed(self):
        with mock.patch("apps.iam.services.MAX_ROWS", 3):
            rows = [_row(tab_number=str(1000 + i)) for i in range(3)]
            report = import_personnel(_build_xlsx(rows))
        self.assertEqual(len(report.successes), 3)

    def test_real_limit_constant_matches_tz(self):
        # Сам факт, что константа в коде равна значению из ТЗ 4.6 — без
        # этого мок в тестах выше проверял бы не то число.
        self.assertEqual(MAX_ROWS, 5000)


class PersonnelImportReportTests(TestCase):
    """Отчёт после каждой загрузки — success/updated/errors (задание)."""

    def setUp(self):
        self.head, self.movement, self.track = _make_tree()

    def test_report_csv_has_three_sections_with_correct_rows(self):
        import_personnel(_build_xlsx([_row(tab_number="0001")]))
        rows = [
            _row(tab_number="0001", position="Обновлённая должность"),  # updated
            _row(tab_number="0002"),  # success
            _row(tab_number="0003", department_path="Несуществующий / Путь"),  # error
        ]
        report = import_personnel(_build_xlsx(rows))

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            paths = write_report_csv(report, Path(tmp))
            self.assertEqual(set(paths), {"success", "updated", "errors"})

            success_content = paths["success"].read_text(encoding="utf-8-sig")
            updated_content = paths["updated"].read_text(encoding="utf-8-sig")
            errors_content = paths["errors"].read_text(encoding="utf-8-sig")

            self.assertIn("0002", success_content)
            self.assertNotIn("0001", success_content)

            self.assertIn("0001", updated_content)

            self.assertIn("0003", errors_content)
            self.assertIn("Несуществующий", errors_content)


class PersonnelImportConcurrencyTests(TransactionTestCase):
    """Уникальный констрейнт tab_number реально есть в БД
    (iam_user_personnel_number_key), но приложение должно превращать его
    нарушение в понятную ошибку строки, а не ронять весь импорт (задание:
    «только уникальный индекс защищает от гонки при параллельном
    импорте» — сам индекс уже был, здесь проверяется обработка гонки на
    его фоне). TransactionTestCase + реальные потоки/соединения — как и в
    ConcurrentStatusTransitionTests, обычный TestCase (одна обёрнутая
    транзакция) не даёт по-настоящему параллельно попасть в SELECT/INSERT
    двух вызовов import_personnel."""

    def test_concurrent_import_of_same_new_tab_number_reports_error_not_crash(self):
        # Не полагаемся на _make_tree()/данные из iam.0002_seed_departments:
        # TransactionTestCase чистит таблицы через TRUNCATE после теста, а
        # serialized_rollback (штатный способ пережить это) на практике
        # конфликтует с пересозданием django_content_type между тестами
        # (Django сам пересоздаёт content types в post_migrate после
        # flush, а serialized_rollback пытается восстановить те же строки
        # поверх — IntegrityError на дубле). Строим дерево локально через
        # get_or_create — не зависит от того, что уже есть в БД к моменту
        # запуска этого конкретного теста.
        head, _ = Department.objects.get_or_create(
            name="Аппарат управления", level=Department.Level.HEAD_OFFICE
        )
        Department.objects.get_or_create(
            name="Служба движения", level=Department.Level.SERVICE, parent=head
        )
        results = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                barrier.wait(timeout=5)
                report = import_personnel(_build_xlsx([_row(tab_number="0099999")]))
                results.append(report)
            finally:
                from django.db import connection

                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(User.objects.filter(personnel_number="0099999").count(), 1)
        self.assertEqual(len(results), 2)  # оба вызова вернулись — ни один не упал исключением наружу

        successes = sum(len(r.successes) for r in results)
        updates = sum(len(r.updates) for r in results)
        errors = sum(len(r.errors) for r in results)

        # Ровно один раз запись реально создаётся (INSERT может успешно
        # пройти только один раз для нового tab_number). Что происходит со
        # вторым вызовом — недетерминировано и оба исхода корректны:
        #   а) он успевает увидеть уже созданную запись через свой
        #      filter().first() и обрабатывает её как upsert (updates=1);
        #   б) оба вызова проходят filter().first() и full_clean() до того,
        #      как второй успевает закоммититься, и второй ловит гонку —
        #      либо на validate_unique() (ValidationError), либо на самом
        #      constraint БД при INSERT (IntegrityError) — оба варианта
        #      наш except (ValueError, ValidationError, IntegrityError)
        #      превращает в errors=1, не в падение всего импорта.
        # Раньше тест жёстко ожидал только исход (б) и был флейковым —
        # плавающий тайминг ОС иногда давал (а).
        self.assertEqual(successes, 1)
        self.assertEqual(updates + errors, 1)
