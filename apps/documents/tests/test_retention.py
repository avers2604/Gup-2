import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.iam.models import Department

from ..models import NormativeDocument
from ..retention import (
    RETENTION_MATRIX,
    RetentionCategory,
    RetentionMode,
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
