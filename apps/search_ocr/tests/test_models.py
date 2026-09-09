from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction
from django.test import TestCase

from ..models import ThesaurusCategory, ThesaurusEntry


def _make_entry(**kwargs):
    defaults = dict(
        id="test_entry",
        canonical="Тестовая запись",
        category=ThesaurusCategory.PROCESS,
        short_forms=["ТЗ"],
    )
    defaults.update(kwargs)
    return ThesaurusEntry(**defaults)


class ThesaurusEntryValidationTests(TestCase):
    def test_valid_entry_saves(self):
        entry = _make_entry()
        entry.save()
        self.assertTrue(ThesaurusEntry.objects.filter(pk="test_entry").exists())

    def test_th04_requires_short_form_or_synonym(self):
        entry = _make_entry(short_forms=[], synonyms=[])
        with self.assertRaises(ValidationError):
            entry.save()

    def test_th04_synonym_alone_is_sufficient(self):
        entry = _make_entry(short_forms=[], synonyms=["синоним"])
        entry.save()
        self.assertTrue(ThesaurusEntry.objects.filter(pk="test_entry").exists())

    def test_th06_weight_above_one_rejected(self):
        entry = _make_entry(weight=1.5)
        with self.assertRaises(ValidationError):
            entry.save()

    def test_th06_weight_below_zero_rejected(self):
        entry = _make_entry(weight=-0.1)
        with self.assertRaises(ValidationError):
            entry.save()

    def test_invalid_category_rejected(self):
        entry = _make_entry(category="not.a.real.category")
        with self.assertRaises(ValidationError):
            entry.save()

    def test_invalid_service_rejected(self):
        entry = _make_entry(service="NOT_A_SERVICE")
        with self.assertRaises(ValidationError):
            entry.save()

    def test_null_service_allowed(self):
        entry = _make_entry(service=None)
        entry.save()
        entry.refresh_from_db()
        self.assertIsNone(entry.service)


class ThesaurusEntryCanonicalUniquenessTests(TestCase):
    """TH-01: canonical уникален по всему файлу без учёта регистра.

    save() вызывает full_clean() до обращения к БД (см. модель) — Django
    умеет проверять UniqueConstraint с выражениями (Lower(...)) уже на
    этом уровне, поэтому конфликт всплывает как ValidationError, не
    IntegrityError — сам констрейнт при этом всё равно существует и в БД
    (полноценная защита и в обход ORM), просто конкретно здесь до неё не
    доходит."""

    def test_exact_duplicate_canonical_rejected(self):
        _make_entry(id="first", canonical="Дубль").save()
        with self.assertRaises(ValidationError):
            _make_entry(id="second", canonical="Дубль").save()

    def test_case_insensitive_duplicate_canonical_rejected(self):
        _make_entry(id="first", canonical="Дубль").save()
        with self.assertRaises(ValidationError):
            _make_entry(id="second", canonical="дубль").save()

    def test_different_canonical_is_fine(self):
        _make_entry(id="first", canonical="Первый термин").save()
        _make_entry(id="second", canonical="Второй термин").save()
        self.assertEqual(ThesaurusEntry.objects.count(), 2)

    def test_constraint_is_real_at_db_level_bypassing_full_clean(self):
        # full_clean() ловит нарушение раньше (см. docstring класса), но
        # это не должно означать, что constraint существует только в
        # Python — прямой SQL в обход ORM обязан упереться в ту же защиту.
        _make_entry(id="first", canonical="Дубль в БД").save()
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO search_ocr_thesaurusentry "
                    "(id, canonical, category, service, short_forms, synonyms, synonyms_legacy, "
                    " weight, source, ambiguous, status, created_at, updated_at) "
                    "VALUES ('second', 'дубль в бд', 'process', NULL, "
                    " ARRAY['x']::varchar[], ARRAY[]::varchar[], ARRAY[]::varchar[], "
                    " 1.0, '', false, 'draft', now(), now())"
                )
