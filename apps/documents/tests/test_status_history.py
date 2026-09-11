"""Ведение SCD-2 истории статусов (ТЗ 4.2.3) и сервисный слой записи.

До партии 2 Этапа 2 таблицу `DocumentStatusHistory` не заполняло ничего,
кроме самих тестов: модель, exclusion constraint и вывод в карточке были,
а записывать в неё было некому. Эти тесты фиксируют, что история теперь
ведётся автоматически на любом пути записи.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from apps.audit.models import AuditLog
from apps.iam.models import User

from .. import services
from ..models import DocumentRelation, NormativeDocument
from .factories import make_document
from .test_permissions import make_user

Status = NormativeDocument.Status


class StatusHistoryMaintenanceTests(TestCase):
    def test_creation_opens_first_period(self):
        document = make_document()
        history = list(document.status_history.all())
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].status, Status.DRAFT)
        self.assertIsNone(history[0].period.upper, "Текущий срез должен быть открыт (valid_to = NULL)")

    def test_status_change_closes_previous_and_opens_new(self):
        document = make_document()
        document.status = Status.ACTIVE
        document.save()

        history = list(document.status_history.order_by("period"))
        self.assertEqual([entry.status for entry in history], [Status.DRAFT, Status.ACTIVE])
        self.assertIsNotNone(history[0].period.upper)
        self.assertIsNone(history[1].period.upper)

    def test_periods_touch_without_gap(self):
        # Граница одна на два среза: `[t0, t1)` и `[t1, NULL)`. Две
        # соседние отметки оставили бы дыру, в которой у документа
        # формально не было никакого статуса.
        document = make_document()
        document.status = Status.ACTIVE
        document.save()
        first, second = document.status_history.order_by("period")
        self.assertEqual(first.period.upper, second.period.lower)

    def test_repeated_save_without_status_change_adds_nothing(self):
        document = make_document()
        document.title = "Другое наименование"
        document.save()
        self.assertEqual(document.status_history.count(), 1)

    def test_history_starts_from_now_for_card_without_past(self):
        # Карточка, заведённая до появления механизма (или в обход
        # save()): прошлое взять неоткуда, но дальнейшая история должна
        # вестись.
        document = make_document()
        document.status_history.all().delete()
        document.status = Status.ACTIVE
        document.save()
        history = list(document.status_history.all())
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].status, Status.ACTIVE)

    def test_admin_path_also_writes_history(self):
        # Ключевая причина, по которой синхронизация живёт в save(), а не
        # только в сервисе: статус меняют и админка, и импорт.
        document = make_document()
        document.status = Status.ACTIVE
        document._audit_actor = make_user(role=User.Role.ADMINISTRATOR)
        document.save()
        self.assertEqual(document.status_history.count(), 2)


class ChangeStatusServiceTests(TestCase):
    def setUp(self):
        self.controller = make_user(personnel_number="0010", role=User.Role.CONTROLLER_LAWYER)
        self.document = make_document(reg_number="500-п")

    def test_publishes_draft(self):
        document, previous = services.change_document_status(
            actor=self.controller, document=self.document, new_status=Status.ACTIVE,
        )
        self.assertEqual(previous, Status.DRAFT)
        self.assertEqual(document.status, Status.ACTIVE)

    def test_writes_audit_record_with_actor(self):
        services.change_document_status(
            actor=self.controller, document=self.document, new_status=Status.ACTIVE,
            comment="Приказ подписан",
        )
        entry = AuditLog.objects.get(event_type=AuditLog.EventType.DOCUMENT_PUBLISHED)
        self.assertEqual(entry.actor_personnel_number, self.controller.personnel_number)
        self.assertEqual(entry.details["old_status"], Status.DRAFT)
        self.assertEqual(entry.details["comment"], "Приказ подписан")

    def test_rejects_transition_outside_graph(self):
        with self.assertRaises(ValidationError):
            services.change_document_status(
                actor=self.controller, document=self.document, new_status=Status.REVOKED,
            )

    def test_rejects_transition_to_same_status(self):
        with self.assertRaises(ValidationError):
            services.change_document_status(
                actor=self.controller, document=self.document, new_status=Status.DRAFT,
            )

    def test_methodist_cannot_change_status(self):
        methodist = make_user(personnel_number="0011", role=User.Role.METHODIST)
        with self.assertRaises(PermissionDenied):
            services.change_document_status(
                actor=methodist, document=self.document, new_status=Status.ACTIVE,
            )

    def test_failed_transition_leaves_no_history_trace(self):
        # «Утратил силу» из черновика графом не предусмотрен: документ,
        # не имевший силы, не может её утратить.
        with self.assertRaises(ValidationError):
            services.change_document_status(
                actor=self.controller, document=self.document, new_status=Status.REVOKED,
            )
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, Status.DRAFT)
        self.assertEqual(self.document.status_history.count(), 1)


class PublicationChecksGraphTests(TestCase):
    """ТЗ 4.2.2: «Алгоритм публикации проверяет граф связей на ацикличность»."""

    def setUp(self):
        self.controller = make_user(personnel_number="0012", role=User.Role.CONTROLLER_LAWYER)

    def test_acyclic_graph_publishes(self):
        first = make_document(reg_number="600-п")
        second = make_document(reg_number="601-п")
        DocumentRelation.objects.create(
            from_document=first, to_document=second,
            relation_type=DocumentRelation.RelationType.CANCELS,
        )
        document, _ = services.change_document_status(
            actor=self.controller, document=first, new_status=Status.ACTIVE,
        )
        self.assertEqual(document.status, Status.ACTIVE)

    def test_cycle_inserted_past_validation_blocks_publication(self):
        # Цикл невозможно создать через ORM — DocumentRelation.clean()
        # его ловит. Поэтому второе ребро вставляется сырым SQL, в обход
        # валидации: именно такой путь (миграция данных, чужая
        # интеграция) и оправдывает перепроверку при публикации.
        first = make_document(reg_number="700-п")
        second = make_document(reg_number="701-п")
        DocumentRelation.objects.create(
            from_document=first, to_document=second,
            relation_type=DocumentRelation.RelationType.REFERENCES,
        )
        from django.db import connection

        table = DocumentRelation._meta.db_table
        with connection.cursor() as cursor:
            # Подавление B608 ниже: тест намеренно обходит ORM, чтобы
            # проверить последний рубеж (ограничение БД) там, куда
            # прикладная валидация не дошла. Подставляется только имя
            # таблицы из _meta.db_table, значения переданы через %s.
            cursor.execute(
                f"INSERT INTO {table} (from_document_id, to_document_id, relation_type, note, created_at) "  # nosec B608
                "VALUES (%s, %s, %s, '', NOW())",
                [str(second.pk), str(first.pk), DocumentRelation.RelationType.REFERENCES],
            )

        with self.assertRaises(ValidationError) as caught:
            services.change_document_status(
                actor=self.controller, document=first, new_status=Status.ACTIVE,
            )
        self.assertIn("цикл", str(caught.exception))

    def test_cycle_does_not_block_non_publishing_transition(self):
        # Отмена документа с испорченным графом связей должна оставаться
        # возможной: иначе цикл, попавший в базу в обход валидации,
        # намертво запирает карточку.
        document = make_document(reg_number="800-п", status=Status.ACTIVE)
        other = make_document(reg_number="801-п")
        DocumentRelation.objects.create(
            from_document=document, to_document=other,
            relation_type=DocumentRelation.RelationType.REFERENCES,
        )
        from django.db import connection

        table = DocumentRelation._meta.db_table
        with connection.cursor() as cursor:
            # Подавление B608 ниже: тот же намеренный обход ORM, что и выше.
            cursor.execute(
                f"INSERT INTO {table} (from_document_id, to_document_id, relation_type, note, created_at) "  # nosec B608
                "VALUES (%s, %s, %s, '', NOW())",
                [str(other.pk), str(document.pk), DocumentRelation.RelationType.REFERENCES],
            )

        updated, _ = services.change_document_status(
            actor=self.controller, document=document, new_status=Status.REVOKED,
        )
        self.assertEqual(updated.status, Status.REVOKED)


class CreateAndUpdateServiceTests(TestCase):
    def setUp(self):
        self.methodist = make_user(personnel_number="0020", role=User.Role.METHODIST)

    def _attrs(self, **overrides):
        import datetime

        from apps.iam.models import Department

        from ..retention import RetentionCategory

        dept, _ = Department.objects.get_or_create(
            name="Служба движения", defaults={"level": Department.Level.SERVICE}
        )
        attrs = dict(
            reg_number="900-п", reg_date=datetime.date(2026, 3, 1),
            effective_date=datetime.date(2026, 3, 10),
            doc_type=NormativeDocument.DocType.ORDER, title="Новая карточка",
            issuer_dept=dept, retention_category=RetentionCategory.ORDERS_CORE,
            # files_original обязателен по ТЗ, и сервис проверяет карточку
            # через full_clean(). Имя-строка не пишет ничего в хранилище и
            # не запускает антивирус (см. antivirus.needs_scan) — ровно то,
            # что нужно тесту, которому важна валидность, а не файл.
            files_original="documents/originals/2026/03/scan.pdf",
        )
        attrs.update(overrides)
        return attrs

    def test_created_document_is_always_a_draft(self):
        document = services.create_document(
            actor=self.methodist, **self._attrs(status=Status.ACTIVE)
        )
        self.assertEqual(document.status, Status.DRAFT)

    def test_reader_cannot_create(self):
        reader = make_user(personnel_number="0021")
        with self.assertRaises(PermissionDenied):
            services.create_document(actor=reader, **self._attrs())

    def test_update_rejected_for_document_in_force(self):
        document = make_document(reg_number="901-п", status=Status.ACTIVE)
        with self.assertRaises(PermissionDenied):
            services.update_document(
                actor=self.methodist, document=document, title="Правка задним числом",
            )

    def test_update_draft_succeeds(self):
        document = make_document(
            reg_number="902-п", files_original="documents/originals/2026/01/scan.pdf",
        )
        services.update_document(
            actor=self.methodist, document=document, title="Уточнённое наименование",
        )
        document.refresh_from_db()
        self.assertEqual(document.title, "Уточнённое наименование")


class RollbackAndAnnulmentTests(TestCase):
    """Исправление ошибочной публикации (решение Заказчика).

    Два разных исхода вместо одного: откат в черновик — документ вернётся
    в работу; аннулирование — публикация признана недействительной и
    документ не возвращается. «Утратил силу» не годится ни для того, ни
    для другого: он утверждает, что документ действовал.
    """

    def setUp(self):
        self.controller = make_user(personnel_number="0500", role=User.Role.CONTROLLER_LAWYER)
        self.administrator = make_user(personnel_number="0501", role=User.Role.ADMINISTRATOR)
        self.document = make_document(reg_number="900-п", status=Status.ACTIVE)

    def test_controller_cannot_roll_back(self):
        # Публиковал он же — исправлять собственную ошибку бесследно не должен.
        with self.assertRaises(PermissionDenied):
            services.change_document_status(
                actor=self.controller, document=self.document,
                new_status=Status.DRAFT, comment="Ошибка публикации",
            )

    def test_controller_cannot_annul(self):
        with self.assertRaises(PermissionDenied):
            services.change_document_status(
                actor=self.controller, document=self.document,
                new_status=Status.ANNULLED, comment="Публикация недействительна",
            )

    def test_administrator_rolls_back_to_draft(self):
        document, previous = services.change_document_status(
            actor=self.administrator, document=self.document,
            new_status=Status.DRAFT, comment="Ушёл не тот файл",
        )
        self.assertEqual(previous, Status.ACTIVE)
        self.assertEqual(document.status, Status.DRAFT)

    def test_rollback_writes_its_own_event(self):
        # Именно эти записи проверяющий ищет в журнале в первую очередь —
        # находить их вперемешку с рутинными сменами статуса значит не
        # находить.
        services.change_document_status(
            actor=self.administrator, document=self.document,
            new_status=Status.DRAFT, comment="Ушёл не тот файл",
        )
        entry = AuditLog.objects.get(
            event_type=AuditLog.EventType.DOCUMENT_PUBLICATION_ROLLED_BACK
        )
        self.assertEqual(entry.details["comment"], "Ушёл не тот файл")
        self.assertEqual(entry.actor_personnel_number, "0501")

    def test_annulment_writes_its_own_event(self):
        services.change_document_status(
            actor=self.administrator, document=self.document,
            new_status=Status.ANNULLED, comment="Приказ не подписан",
        )
        self.assertTrue(
            AuditLog.objects.filter(event_type=AuditLog.EventType.DOCUMENT_ANNULLED).exists()
        )

    def test_reason_is_mandatory(self):
        for new_status in (Status.DRAFT, Status.ANNULLED):
            with self.subTest(new_status=new_status):
                with self.assertRaises(ValidationError):
                    services.change_document_status(
                        actor=self.administrator, document=self.document,
                        new_status=new_status, comment="   ",
                    )

    def test_ordinary_transition_needs_no_reason(self):
        document, _ = services.change_document_status(
            actor=self.controller, document=self.document, new_status=Status.REVOKED,
        )
        self.assertEqual(document.status, Status.REVOKED)

    def test_first_draft_save_is_not_a_rollback(self):
        # Событие отката отличается не новым статусом, а тем, откуда он.
        make_document(reg_number="901-п")
        self.assertFalse(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.DOCUMENT_PUBLICATION_ROLLED_BACK
            ).exists()
        )

    def test_rollback_closes_the_active_period_in_history(self):
        services.change_document_status(
            actor=self.administrator, document=self.document,
            new_status=Status.DRAFT, comment="Ошибка публикации",
        )
        statuses = list(self.document.status_history.order_by("period").values_list("status", flat=True))
        self.assertEqual(statuses[-1], Status.DRAFT)


class AmendedReturnsToActiveTests(TestCase):
    """Решение Заказчика: отмена всех изменяющих документов возвращает
    базовый документ в исходную редакцию."""

    def test_controller_returns_amended_document_to_active(self):
        controller = make_user(personnel_number="0510", role=User.Role.CONTROLLER_LAWYER)
        document = make_document(reg_number="910-п", status=Status.ACTIVE_AMENDED)
        updated, previous = services.change_document_status(
            actor=controller, document=document, new_status=Status.ACTIVE,
        )
        self.assertEqual(previous, Status.ACTIVE_AMENDED)
        self.assertEqual(updated.status, Status.ACTIVE)
