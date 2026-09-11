from django.db import DatabaseError, connection, transaction
from django.test import TestCase

from .models import AuditLog


class AuditLogWormTests(TestCase):
    """Журнал аудита неизменяем: ни одна запись не должна поддаваться
    изменению или удалению (ТЗ 4.3.1, 4.7) — ни через ORM, ни в обход него."""

    def _create_entry(self, object_id="142-п"):
        return AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_PUBLISHED,
            actor_personnel_number="0001",
            object_type="NormativeDocument",
            object_id=object_id,
        )

    def test_create_succeeds(self):
        entry = self._create_entry()
        self.assertTrue(AuditLog.objects.filter(pk=entry.pk).exists())

    def test_read_after_create_is_not_blocked(self):
        # WORM запрещает изменение и удаление, но не чтение — экспорт по
        # GET /api/v1/audit/export (ТЗ §7) должен работать без ограничений.
        self._create_entry(object_id="142-п")
        self._create_entry(object_id="143-п")
        self.assertEqual(AuditLog.objects.count(), 2)
        self.assertTrue(AuditLog.objects.filter(object_id="142-п").exists())

    def test_instance_save_update_forbidden(self):
        entry = self._create_entry()
        entry.object_id = "143-п"
        with self.assertRaises(PermissionError):
            entry.save()

    def test_instance_delete_forbidden(self):
        entry = self._create_entry()
        with self.assertRaises(PermissionError):
            entry.delete()

    def test_queryset_update_forbidden(self):
        self._create_entry()
        with self.assertRaises(PermissionError):
            AuditLog.objects.all().update(object_id="143-п")

    def test_queryset_delete_forbidden(self):
        self._create_entry()
        with self.assertRaises(PermissionError):
            AuditLog.objects.all().delete()

    def test_bulk_update_forbidden(self):
        # bulk_update() — отдельный от update() метод QuerySet, реализованный
        # через собственный UPDATE-запрос; переопределение update() его не
        # перехватывает, поэтому это отдельная проверка.
        entry = self._create_entry()
        entry.object_id = "143-п"
        with self.assertRaises(PermissionError):
            AuditLog.objects.bulk_update([entry], ["object_id"])

    def test_bulk_create_with_update_conflicts_forbidden(self):
        entry = AuditLog(
            event_type=AuditLog.EventType.DOCUMENT_PUBLISHED,
            object_type="NormativeDocument",
            object_id="142-п",
        )
        with self.assertRaises(PermissionError):
            AuditLog.objects.bulk_create(
                [entry], update_conflicts=True, update_fields=["object_id"], unique_fields=["id"]
            )

    def test_plain_bulk_create_allowed(self):
        # Обычный bulk_create — это только вставка новых записей, WORM не нарушает.
        entries = [
            AuditLog(event_type=AuditLog.EventType.DOCUMENT_PUBLISHED, object_id="142-п"),
            AuditLog(event_type=AuditLog.EventType.DOCUMENT_PUBLISHED, object_id="143-п"),
        ]
        AuditLog.objects.bulk_create(entries)
        self.assertEqual(AuditLog.objects.count(), 2)


class AuditLogDatabaseLevelWormTests(TestCase):
    """Защита должна держаться и при обходе Django ORM — прямым SQL
    (ТЗ 4.7: WORM на уровне БД, не только на уровне приложения)."""

    def test_raw_sql_update_is_rejected_by_db_trigger(self):
        AuditLog.objects.create(event_type=AuditLog.EventType.DOCUMENT_PUBLISHED, object_id="142-п")
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("UPDATE audit_auditlog SET object_id = 'hacked'")

    def test_raw_sql_delete_is_rejected_by_db_trigger(self):
        AuditLog.objects.create(event_type=AuditLog.EventType.DOCUMENT_PUBLISHED, object_id="142-п")
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM audit_auditlog")

    def test_raw_sql_select_still_works(self):
        AuditLog.objects.create(event_type=AuditLog.EventType.DOCUMENT_PUBLISHED, object_id="142-п")
        with connection.cursor() as cursor:
            cursor.execute("SELECT object_id FROM audit_auditlog")
            rows = cursor.fetchall()
        self.assertEqual(rows, [("142-п",)])

    def test_raw_sql_truncate_is_rejected_by_db_trigger_when_table_has_rows(self):
        # BEFORE UPDATE/DELETE — row-level, TRUNCATE вообще не подпадает
        # под них в Postgres. Нужен отдельный STATEMENT-level триггер
        # (audit.0005_worm_truncate_trigger) — без него TRUNCATE обошёл бы
        # WORM-защиту полностью, стерев журнал одной командой.
        AuditLog.objects.create(event_type=AuditLog.EventType.DOCUMENT_PUBLISHED, object_id="142-п")
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("TRUNCATE audit_auditlog")
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_raw_sql_truncate_allowed_when_table_is_empty(self):
        # DELETE уже невозможен (0003) — единственный способ таблице стать
        # пустой снова — если в неё ещё никогда не писали. TRUNCATE пустой
        # таблицы не теряет ни одной записи, поэтому безопасен и намеренно
        # разрешён — иначе триггер ломает штатный flush Django между
        # TransactionTestCase-тестами (сам flush использует TRUNCATE).
        self.assertEqual(AuditLog.objects.count(), 0)
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE audit_auditlog")  # не должно бросать исключение


# ---------------------------------------------------------------------------
# Web GUI журнала аудита и личный кабинет (ТЗ 4.7, п.8.2) — партия 5 Этапа 2.
#
# Журнал вёлся с Этапа 1, но прочитать его можно было только в Django
# admin: Офицеру ИБ требовался is_staff, а сама роль на доступ к журналу
# не влияла никак.
# ---------------------------------------------------------------------------
from django.test import Client  # noqa: E402
from django.urls import reverse  # noqa: E402

from apps.documents.tests.test_permissions import make_user  # noqa: E402
from apps.iam.models import User  # noqa: E402

from . import permissions as audit_permissions  # noqa: E402

PASSWORD = "Sup3r$ecret!Pass"


class AuditPermissionTests(TestCase):
    def test_reader_has_no_access(self):
        self.assertFalse(audit_permissions.can_view_audit_log(make_user()))

    def test_security_officer_has_access(self):
        user = make_user(personnel_number="0400", role=User.Role.SECURITY_OFFICER)
        self.assertTrue(audit_permissions.can_view_audit_log(user))

    def test_administrator_has_access(self):
        user = make_user(personnel_number="0401", role=User.Role.ADMINISTRATOR)
        self.assertTrue(audit_permissions.can_view_audit_log(user))

    def test_controller_lawyer_has_no_access(self):
        # Намеренно: журнал показывает в том числе его собственные
        # действия, и «проверяющий, читающий журнал своих действий» —
        # это не разделение обязанностей.
        user = make_user(personnel_number="0402", role=User.Role.CONTROLLER_LAWYER)
        self.assertFalse(audit_permissions.can_view_audit_log(user))


class AuditLogViewTests(TestCase):
    def setUp(self):
        self.officer = make_user(personnel_number="0410", role=User.Role.SECURITY_OFFICER)
        self.reader = make_user(personnel_number="0411")
        AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_PUBLISHED,
            actor_personnel_number="0999", object_type="NormativeDocument",
            object_id="A1-п", details={"old_status": "draft", "new_status": "active"},
        )
        AuditLog.objects.create(
            event_type=AuditLog.EventType.SESSION_LOGIN,
            actor_personnel_number="0888", object_type="User", object_id="u1",
        )

    def _client(self, user):
        client = Client()
        client.login(personnel_number=user.personnel_number, password=PASSWORD)
        return client

    def test_requires_login(self):
        self.assertEqual(Client().get(reverse("audit:list")).status_code, 302)

    def test_reader_gets_404(self):
        self.assertEqual(self._client(self.reader).get(reverse("audit:list")).status_code, 404)

    def test_officer_sees_entries(self):
        response = self._client(self.officer).get(reverse("audit:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A1-п")
        self.assertContains(response, "Документ опубликован")

    def test_filter_by_event_type(self):
        response = self._client(self.officer).get(
            reverse("audit:list"), {"event_type": AuditLog.EventType.SESSION_LOGIN}
        )
        self.assertContains(response, "0888")
        self.assertNotContains(response, "A1-п")

    def test_filter_by_object_id(self):
        response = self._client(self.officer).get(reverse("audit:list"), {"object_id": "A1-п"})
        self.assertContains(response, "A1-п")
        self.assertNotContains(response, "0888")

    def test_reversed_period_reports_error(self):
        response = self._client(self.officer).get(
            reverse("audit:list"), {"date_from": "2030-01-01", "date_to": "2020-01-01"}
        )
        self.assertContains(response, "Начало периода позже его окончания.")

    def test_date_to_is_inclusive(self):
        # Пользователь, выбравший «по сегодня», ожидает увидеть события
        # сегодняшнего дня, а не пустой список.
        from django.utils import timezone

        today = timezone.localdate().isoformat()
        response = self._client(self.officer).get(reverse("audit:list"), {"date_to": today})
        self.assertContains(response, "A1-п")

    def test_navigation_link_visible_only_to_officer(self):
        officer_page = self._client(self.officer).get(reverse("audit:list"))
        self.assertContains(officer_page, "Журнал аудита")
        reader_page = self._client(self.reader).get(reverse("documents:list"))
        self.assertNotContains(reader_page, "Журнал аудита")


class ProfileViewTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0420")
        self.client = Client()
        self.client.login(personnel_number="0420", password=PASSWORD)

    def test_requires_login(self):
        self.assertEqual(Client().get(reverse("iam:profile")).status_code, 302)

    def test_shows_role_and_clearance(self):
        response = self.client.get(reverse("iam:profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Читатель")
        self.assertContains(response, "Допуск к документам «ДСП»")

    def test_shows_only_own_login_events(self):
        other = make_user(personnel_number="0421")
        AuditLog.objects.create(
            event_type=AuditLog.EventType.SESSION_LOGIN,
            actor=other, actor_personnel_number="0421",
            details={"ip_address": "10.0.0.9"},
        )
        AuditLog.objects.create(
            event_type=AuditLog.EventType.SESSION_LOGIN,
            actor=self.user, actor_personnel_number="0420",
            details={"ip_address": "10.0.0.1"},
        )
        response = self.client.get(reverse("iam:profile"))
        self.assertContains(response, "10.0.0.1")
        self.assertNotContains(response, "10.0.0.9")

    def test_password_expiry_absent_when_never_changed(self):
        self.user.password_changed_at = None
        self.user.save(update_fields=["password_changed_at"])
        response = self.client.get(reverse("iam:profile"))
        self.assertContains(response, "ещё ни разу не менялся")


class AuditDetailsRenderingTests(TestCase):
    """Реквизиты события — свободный JSON, и одна запись может унести в
    него сотни значений (импорт тезауруса). Без обрезки такая строка
    вырастает выше всей страницы и хоронит под собой журнал.

    Проверяется отрендеренная страница, а не шаблон: важно, что в ответ
    не уходит простыня, каким бы способом её ни обрезали.
    """

    def test_long_details_value_is_truncated_in_the_table(self):
        officer = make_user(personnel_number="0430", role=User.Role.SECURITY_OFFICER)
        long_value = "термин-" * 500
        AuditLog.objects.create(
            event_type=AuditLog.EventType.THESAURUS_UPDATED,
            object_type="ThesaurusEntry", object_id="bulk_import",
            details={"created": long_value},
        )
        client = Client()
        client.login(personnel_number="0430", password=PASSWORD)
        body = client.get(reverse("audit:list")).content.decode()

        # Полное значение остаётся доступным подсказкой, но в самой
        # ячейке его быть не должно.
        cell = body.split('<span class="caption">created:</span>')[1][:400]
        self.assertNotIn(long_value, cell)
        self.assertIn("…", cell)


class AuditLogExportTests(TestCase):
    """Выгрузка журнала (ТЗ 4.7): доступ, самофиксация и безопасность файла."""

    def setUp(self):
        self.officer = make_user(personnel_number="0420", role=User.Role.SECURITY_OFFICER)
        self.reader = make_user(personnel_number="0421")
        AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_PUBLISHED,
            actor_personnel_number="0999", object_type="NormativeDocument",
            object_id="B1-п", details={"reg_number": "B1-п"},
        )
        AuditLog.objects.create(
            event_type=AuditLog.EventType.SESSION_LOGIN,
            actor_personnel_number="0888", object_type="User", object_id="u2",
        )

    def _client(self, user):
        client = Client()
        client.login(personnel_number=user.personnel_number, password=PASSWORD)
        return client

    @staticmethod
    def _body(response):
        return b"".join(response.streaming_content).decode("utf-8")

    def test_requires_login(self):
        self.assertEqual(Client().get(reverse("audit:export")).status_code, 302)

    def test_reader_gets_404_like_the_log_itself(self):
        response = self._client(self.reader).get(reverse("audit:export"))
        self.assertEqual(response.status_code, 404)

    def test_officer_downloads_csv_attachment(self):
        response = self._client(self.officer).get(reverse("audit:export"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("attachment;", response["Content-Disposition"])
        body = self._body(response)
        self.assertTrue(body.startswith("﻿"), "нужен BOM, иначе Excel ломает кириллицу")
        self.assertIn("B1-п", body)
        self.assertIn("u2", body)

    def test_export_records_itself_in_the_log(self):
        """Выгрузка журнала безопасности сама является событием безопасности."""
        before = AuditLog.objects.filter(
            event_type=AuditLog.EventType.AUDIT_LOG_EXPORTED
        ).count()

        response = self._client(self.officer).get(
            reverse("audit:export"), {"event_type": AuditLog.EventType.SESSION_LOGIN}
        )
        self._body(response)

        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.AUDIT_LOG_EXPORTED)
        self.assertEqual(entries.count(), before + 1)
        entry = entries.latest("created_at")
        self.assertEqual(entry.actor_personnel_number, "0420")
        # Применённые фильтры фиксируются: проверяющему важно, какой именно
        # срез журнала вынесли наружу, а не только сам факт выгрузки.
        self.assertEqual(entry.details["filters"]["event_type"], AuditLog.EventType.SESSION_LOGIN)
        self.assertEqual(entry.details["matched_entries"], 1)

    def test_filters_apply_to_the_file_not_only_to_the_screen(self):
        response = self._client(self.officer).get(
            reverse("audit:export"), {"event_type": AuditLog.EventType.SESSION_LOGIN}
        )
        body = self._body(response)

        self.assertIn("u2", body)
        self.assertNotIn("B1-п", body)

    def test_formula_injection_is_neutralised(self):
        """Значение из журнала не должно исполниться при открытии в Excel."""
        # Классический DDE-вектор; уложен в max_length поля табельного номера.
        AuditLog.objects.create(
            event_type=AuditLog.EventType.SESSION_LOGIN_FAILED,
            actor_personnel_number="=cmd|'/c calc'!A1",
            object_type="User", object_id="u3",
        )

        body = self._body(self._client(self.officer).get(reverse("audit:export")))

        self.assertIn("'=cmd|", body, "опасное значение должно быть экранировано апострофом")
        self.assertNotIn(";=cmd|", body, "неэкранированная формула не должна попасть в ячейку")
