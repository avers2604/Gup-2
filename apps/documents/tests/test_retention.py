import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.audit.models import AuditLog
from apps.iam.models import Department

from ..models import NormativeDocument
from ..retention import (
    RETENTION_MATRIX,
    RetentionCategory,
    RetentionMode,
    is_expired_at_intake,
    is_retention_still_binding,
    object_lock_params_for,
    resolve_retention_until,
    suggest_retention_category,
)
from .factories import make_document


class RetentionResolverTests(TestCase):
    """apps/documents/retention.py — чистая логика, без БД: матрица
    (срок/режим/основание) присланная Заказчиком, закрывает открытые
    вопросы №1-2 плана."""

    def test_permanent_category_has_no_retention_until(self):
        self.assertIsNone(
            resolve_retention_until(RetentionCategory.ORDERS_CORE, reg_date=datetime.date(2026, 1, 1))
        )

    def test_personnel_order_after_2003_uses_50_years(self):
        until = resolve_retention_until(
            RetentionCategory.ORDERS_PERSONNEL, reg_date=datetime.date(2020, 5, 10)
        )
        self.assertEqual(until, datetime.date(2070, 5, 10))

    def test_personnel_order_before_2003_uses_75_years(self):
        # Ст. 22.1 ФЗ-125: для документов до 2003 г. — 75 лет, а не 50.
        until = resolve_retention_until(
            RetentionCategory.ORDERS_PERSONNEL, reg_date=datetime.date(1998, 3, 1)
        )
        self.assertEqual(until, datetime.date(2073, 3, 1))

    def test_directives_operational_5_years(self):
        until = resolve_retention_until(
            RetentionCategory.DIRECTIVES_OPERATIONAL, reg_date=datetime.date(2026, 1, 1)
        )
        self.assertEqual(until, datetime.date(2031, 1, 1))

    def test_leap_day_reg_date_does_not_crash(self):
        # 29 февраля + N лет может попасть на невисокосный год.
        until = resolve_retention_until(
            RetentionCategory.DIRECTIVES_OPERATIONAL, reg_date=datetime.date(2024, 2, 29)
        )
        self.assertEqual(until, datetime.date(2029, 2, 28))

    def test_dsp_without_declassification_date_returns_none(self):
        # None здесь значит «не может быть посчитан», а не «бессрочно» —
        # разница видна через RETENTION_MATRIX[category].conditional_on_declassification.
        until = resolve_retention_until(RetentionCategory.DSP, reg_date=datetime.date(2020, 1, 1))
        self.assertIsNone(until)
        self.assertTrue(RETENTION_MATRIX[RetentionCategory.DSP].conditional_on_declassification)

    def test_dsp_with_declassification_date_adds_30_years(self):
        until = resolve_retention_until(
            RetentionCategory.DSP,
            reg_date=datetime.date(2020, 1, 1),
            declassification_date=datetime.date(2025, 6, 1),
        )
        self.assertEqual(until, datetime.date(2055, 6, 1))

    def test_all_matrix_entries_have_governance_or_compliance_mode(self):
        for policy in RETENTION_MATRIX.values():
            self.assertIn(policy.mode, {RetentionMode.GOVERNANCE, RetentionMode.COMPLIANCE})

    def test_suggest_dsp_from_access_level(self):
        self.assertEqual(
            suggest_retention_category(access_level="restricted", reg_date=datetime.date(2026, 1, 1)),
            RetentionCategory.DSP,
        )

    def test_suggest_archival_from_old_reg_date(self):
        self.assertEqual(
            suggest_retention_category(access_level="general", reg_date=datetime.date(2010, 1, 1)),
            RetentionCategory.ARCHIVAL_SCANS,
        )

    def test_suggest_none_when_not_derivable(self):
        # Обычный современный общий документ — категория не выводится
        # автоматически, это решение человека (см. docstring модуля).
        self.assertIsNone(
            suggest_retention_category(access_level="general", reg_date=datetime.date(2026, 1, 1))
        )


class NormativeDocumentRetentionFieldTests(TestCase):
    """Поля retention_mode/retention_until на карточке НРД считаются
    автоматически из retention_category в save() — вручную не задаются
    (editable=False)."""

    def test_save_computes_mode_and_until_from_category(self):
        doc = make_document(retention_category=RetentionCategory.DIRECTIVES_OPERATIONAL)
        self.assertEqual(doc.retention_mode, RetentionMode.GOVERNANCE)
        self.assertEqual(doc.retention_until, datetime.date(2031, 1, 1))

    def test_save_computes_permanent_category_as_null_until(self):
        doc = make_document(retention_category=RetentionCategory.ORDERS_CORE)
        self.assertEqual(doc.retention_mode, RetentionMode.COMPLIANCE)
        self.assertIsNone(doc.retention_until)

    def test_dsp_category_uses_declassification_date(self):
        doc = make_document(
            retention_category=RetentionCategory.DSP,
            declassification_date=datetime.date(2030, 1, 1),
        )
        self.assertEqual(doc.retention_until, datetime.date(2060, 1, 1))

    def test_inapplicable_category_rejected(self):
        # TEMPLATES_APPROVED — для Template (и то пока не подключена, см.
        # retention.py), ACTS_INVESTIGATION/PERMITS_EH — для ещё не
        # реализованной модели актов. Ни одна из трёх не должна
        # проходить на карточке НРД. Инстанс не сохраняется в БД —
        # full_clean() на несохранённом объекте, files_original исключён
        # из проверки: тест про retention_category, а не про файл.
        dept = Department.objects.get(name="Служба движения")
        for bad_category in (
            RetentionCategory.TEMPLATES_APPROVED,
            RetentionCategory.ACTS_INVESTIGATION,
            RetentionCategory.PERMITS_EH,
        ):
            doc = NormativeDocument(
                reg_number="bad",
                reg_date=datetime.date(2026, 1, 1),
                effective_date=datetime.date(2026, 1, 2),
                doc_type=NormativeDocument.DocType.ORDER,
                title="Тест неприменимой категории",
                issuer_dept=dept,
                retention_category=bad_category,
            )
            with self.assertRaises(ValidationError) as ctx:
                doc.full_clean(exclude=["files_original"])
            self.assertIn("retention_category", ctx.exception.message_dict)

    def test_resaving_recomputes_retention_until_if_category_changed(self):
        doc = make_document(retention_category=RetentionCategory.DIRECTIVES_OPERATIONAL)
        self.assertEqual(doc.retention_until, datetime.date(2031, 1, 1))

        doc.retention_category = RetentionCategory.ORDERS_CORE
        doc.save()
        doc.refresh_from_db()
        self.assertEqual(doc.retention_mode, RetentionMode.COMPLIANCE)
        self.assertIsNone(doc.retention_until)

    def test_category_change_with_update_fields_persists_derived_fields(self):
        doc = make_document(retention_category=RetentionCategory.DIRECTIVES_OPERATIONAL)

        doc.retention_category = RetentionCategory.ORDERS_PERSONNEL
        doc.save(update_fields={"retention_category"})
        doc.refresh_from_db()

        self.assertEqual(doc.retention_mode, RetentionMode.COMPLIANCE)
        self.assertEqual(doc.retention_until, datetime.date(2076, 1, 1))

    def test_resaving_without_category_change_does_not_recompute_retention_until(self):
        # Раньше retention_until пересчитывался на КАЖДЫЙ save() — дата могла
        # "уехать", если карточку просто пересохранили спустя время с уже
        # изменившимся reg_date. Теперь пересчёт — только при первом
        # сохранении или реальной смене категории.
        doc = make_document(retention_category=RetentionCategory.DIRECTIVES_OPERATIONAL)
        original_until = doc.retention_until
        self.assertEqual(original_until, datetime.date(2031, 1, 1))

        doc.reg_date = datetime.date(2000, 1, 1)  # если бы пересчитывалось — дало бы 2005-01-01
        doc.save()
        doc.refresh_from_db()
        self.assertEqual(doc.retention_until, original_until)

    def test_category_change_after_creation_is_audited(self):
        doc = make_document(retention_category=RetentionCategory.DIRECTIVES_OPERATIONAL)

        doc.retention_category = RetentionCategory.ORDERS_CORE
        doc.save()

        entries = AuditLog.objects.filter(
            event_type=AuditLog.EventType.DOCUMENT_RETENTION_CATEGORY_CHANGED,
            object_id=doc.reg_number,
        )
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().details["old_category"], RetentionCategory.DIRECTIVES_OPERATIONAL)
        self.assertEqual(entries.first().details["new_category"], RetentionCategory.ORDERS_CORE)

    def test_initial_creation_does_not_log_category_changed(self):
        # Первое присвоение категории — не "смена", это классификация с нуля.
        make_document(retention_category=RetentionCategory.DIRECTIVES_OPERATIONAL)
        self.assertFalse(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.DOCUMENT_RETENTION_CATEGORY_CHANGED
            ).exists()
        )

    def test_resaving_same_category_does_not_log_category_changed(self):
        doc = make_document(retention_category=RetentionCategory.DIRECTIVES_OPERATIONAL)
        doc.title = "Обновлённое наименование"
        doc.save()
        self.assertFalse(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.DOCUMENT_RETENTION_CATEGORY_CHANGED
            ).exists()
        )

    def test_expired_at_intake_logs_audit_warning(self):
        # DIRECTIVES_OPERATIONAL — 5 лет; регистрация "задним числом" с
        # 2000 годом даёт retention_until=2005-01-01, что уже в прошлом.
        doc = make_document(
            reg_number="старый-142",
            reg_date=datetime.date(2000, 1, 1),
            retention_category=RetentionCategory.DIRECTIVES_OPERATIONAL,
        )
        self.assertEqual(doc.retention_until, datetime.date(2005, 1, 1))

        entries = AuditLog.objects.filter(
            event_type=AuditLog.EventType.DOCUMENT_RETENTION_EXPIRED_AT_INTAKE,
            object_id=doc.reg_number,
        )
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().details["retention_until"], "2005-01-01")

    def test_not_expired_at_intake_does_not_log_warning(self):
        make_document(retention_category=RetentionCategory.DIRECTIVES_OPERATIONAL)
        self.assertFalse(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.DOCUMENT_RETENTION_EXPIRED_AT_INTAKE
            ).exists()
        )

    def test_permanent_category_never_logs_expired_at_intake(self):
        # retention_until=None ("постоянно") никогда не может быть "уже
        # истёкшим" — is_expired_at_intake(None) всегда False.
        make_document(
            reg_number="старый-permanent",
            reg_date=datetime.date(1990, 1, 1),
            retention_category=RetentionCategory.ORDERS_CORE,
        )
        self.assertFalse(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.DOCUMENT_RETENTION_EXPIRED_AT_INTAKE
            ).exists()
        )


class IsRetentionStillBindingTests(TestCase):
    """None должен всегда означать «блокировка ещё действует» — не
    «ограничений нет» (постоянное хранение ИЛИ срок ещё не посчитан, см.
    docstring функции)."""

    def test_none_is_always_binding(self):
        self.assertTrue(is_retention_still_binding(None))
        self.assertTrue(
            is_retention_still_binding(None, as_of=datetime.date(2200, 1, 1))
        )

    def test_future_date_is_binding(self):
        self.assertTrue(
            is_retention_still_binding(
                datetime.date(2030, 1, 1), as_of=datetime.date(2026, 1, 1)
            )
        )

    def test_past_date_is_not_binding(self):
        self.assertFalse(
            is_retention_still_binding(
                datetime.date(2020, 1, 1), as_of=datetime.date(2026, 1, 1)
            )
        )

    def test_exact_date_is_still_binding(self):
        # Дата истечения — последний день, когда блокировка ещё действует.
        d = datetime.date(2026, 1, 1)
        self.assertTrue(is_retention_still_binding(d, as_of=d))


class IsExpiredAtIntakeTests(TestCase):
    def test_none_is_never_expired(self):
        self.assertFalse(is_expired_at_intake(None))
        self.assertFalse(is_expired_at_intake(None, as_of=datetime.date(2200, 1, 1)))

    def test_past_date_is_expired(self):
        self.assertTrue(
            is_expired_at_intake(datetime.date(2020, 1, 1), as_of=datetime.date(2026, 1, 1))
        )

    def test_future_date_is_not_expired(self):
        self.assertFalse(
            is_expired_at_intake(datetime.date(2030, 1, 1), as_of=datetime.date(2026, 1, 1))
        )

    def test_exact_date_is_not_yet_expired(self):
        d = datetime.date(2026, 1, 1)
        self.assertFalse(is_expired_at_intake(d, as_of=d))


class ObjectLockParamsForTests(TestCase):
    """Чистая функция-подготовка параметров S3 Object Lock — сам MinIO-клиент
    ещё не реализован (см. docstring), но контракт значений фиксируется
    тестами уже сейчас."""

    def test_permanent_retention_uses_legal_hold_not_far_future_date(self):
        policy = RETENTION_MATRIX[RetentionCategory.ORDERS_CORE]
        params = object_lock_params_for(policy, None)
        self.assertEqual(params["ObjectLockLegalHoldStatus"], "ON")
        self.assertIsNone(params["ObjectLockRetainUntilDate"])
        self.assertIsNone(params["ObjectLockMode"])

    def test_governance_dated_retention(self):
        policy = RETENTION_MATRIX[RetentionCategory.DIRECTIVES_OPERATIONAL]
        self.assertEqual(policy.mode, RetentionMode.GOVERNANCE)
        params = object_lock_params_for(policy, datetime.date(2031, 1, 1))
        self.assertEqual(params["ObjectLockMode"], "GOVERNANCE")
        self.assertEqual(params["ObjectLockLegalHoldStatus"], "OFF")
        self.assertEqual(params["ObjectLockRetainUntilDate"], "2031-01-01")

    def test_compliance_dated_retention(self):
        policy = RETENTION_MATRIX[RetentionCategory.ORDERS_PERSONNEL]
        self.assertEqual(policy.mode, RetentionMode.COMPLIANCE)
        params = object_lock_params_for(policy, datetime.date(2070, 5, 10))
        self.assertEqual(params["ObjectLockMode"], "COMPLIANCE")
        self.assertEqual(params["ObjectLockLegalHoldStatus"], "OFF")
        self.assertEqual(params["ObjectLockRetainUntilDate"], "2070-05-10")


class RetentionMatrixStageFlagsTests(TestCase):
    """Категории для ещё не реализованной модели актов (Этап 2) должны
    быть явно помечены неактивными, чтобы их нельзя было присвоить
    документу «случайно» до появления самой карточки."""

    def test_acts_investigation_and_permits_eh_are_inactive(self):
        for category in (RetentionCategory.ACTS_INVESTIGATION, RetentionCategory.PERMITS_EH):
            policy = RETENTION_MATRIX[category]
            self.assertFalse(policy.is_active)
            self.assertIsNotNone(policy.stage)

    def test_categories_in_use_are_active(self):
        for category in (
            RetentionCategory.ORDERS_CORE,
            RetentionCategory.ORDERS_PERSONNEL,
            RetentionCategory.DIRECTIVES_OPERATIONAL,
            RetentionCategory.ARCHIVAL_SCANS,
            RetentionCategory.DSP,
            RetentionCategory.TEMPLATES_APPROVED,
        ):
            self.assertTrue(RETENTION_MATRIX[category].is_active)
