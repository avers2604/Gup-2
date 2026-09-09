import io
import json

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.audit.models import AuditLog

from ..models import ThesaurusEntry
from ..services import import_thesaurus


def _file(entries, ambiguity_registry=None):
    data = {
        "meta": {"categories": {}, "services": {}},
        "entries": entries,
        "ambiguity_registry": ambiguity_registry or [],
    }
    return io.BytesIO(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def _entry(**kwargs):
    defaults = dict(
        id="test_id", canonical="Тестовый термин", category="process",
        short_forms=["ТТ"], status="draft",
    )
    defaults.update(kwargs)
    return defaults


class ImportThesaurusBasicTests(TestCase):
    def test_new_entries_created(self):
        report = import_thesaurus(_file([_entry(id="a", canonical="Термин А"), _entry(id="b", canonical="Термин Б")]))
        self.assertEqual(report.created, ["a", "b"])
        self.assertEqual(report.updated, [])
        self.assertEqual(report.errors, [])
        self.assertEqual(ThesaurusEntry.objects.count(), 2)

    def test_empty_file_rejected(self):
        with self.assertRaises(ValidationError):
            import_thesaurus(_file([]))

    def test_unknown_category_reported_as_error_not_raised(self):
        report = import_thesaurus(_file([_entry(id="a", category="not.a.category")]))
        self.assertEqual(report.created, [])
        self.assertEqual(len(report.errors), 1)
        self.assertEqual(report.errors[0][0], "a")

    def test_unknown_service_reported_as_error(self):
        report = import_thesaurus(_file([_entry(id="a", service="NOT_A_SERVICE")]))
        self.assertEqual(len(report.errors), 1)

    def test_missing_id_reported_as_error(self):
        report = import_thesaurus(_file([_entry(id="")]))
        self.assertEqual(len(report.errors), 1)

    def test_duplicate_id_within_file_second_occurrence_is_error(self):
        report = import_thesaurus(_file([
            _entry(id="dup", canonical="Первое"),
            _entry(id="dup", canonical="Второе"),
        ]))
        self.assertEqual(report.created, ["dup"])
        self.assertEqual(len(report.errors), 1)

    def test_th04_violation_within_import_is_row_error_not_crash(self):
        report = import_thesaurus(_file([_entry(id="a", short_forms=[], synonyms=[])]))
        self.assertEqual(report.created, [])
        self.assertEqual(len(report.errors), 1)

    def test_valid_and_invalid_rows_do_not_block_each_other(self):
        report = import_thesaurus(_file([
            _entry(id="good", canonical="Хороший термин"),
            _entry(id="bad", category="nonsense"),
        ]))
        self.assertEqual(report.created, ["good"])
        self.assertEqual([e[0] for e in report.errors], ["bad"])


class ImportThesaurusUpsertTests(TestCase):
    def test_reimport_unchanged_file_is_a_no_op(self):
        entries = [_entry(id="a", canonical="Термин А")]
        import_thesaurus(_file(entries))
        report = import_thesaurus(_file(entries))
        self.assertEqual(report.created, [])
        self.assertEqual(report.updated, [])
        self.assertEqual(report.unchanged, ["a"])

    def test_changed_field_is_reported_as_update_with_diff(self):
        import_thesaurus(_file([_entry(id="a", canonical="Старое имя")]))
        report = import_thesaurus(_file([_entry(id="a", canonical="Новое имя")]))
        self.assertEqual(report.updated, ["a"])
        self.assertIn("canonical", report.diffs["a"])
        self.assertEqual(report.diffs["a"]["canonical"], ["Старое имя", "Новое имя"])

        entry = ThesaurusEntry.objects.get(pk="a")
        self.assertEqual(entry.canonical, "Новое имя")

    def test_upsert_does_not_touch_unrelated_existing_entries(self):
        # Импорт не выполняет массового удаления/сброса — как и
        # import_personnel, он upsert-ит только то, что реально в файле.
        import_thesaurus(_file([_entry(id="a", canonical="Термин А")]))
        import_thesaurus(_file([_entry(id="b", canonical="Термин Б")]))
        self.assertEqual(ThesaurusEntry.objects.count(), 2)
        self.assertTrue(ThesaurusEntry.objects.filter(pk="a").exists())


class ImportThesaurusAuditTests(TestCase):
    def test_import_with_changes_writes_single_audit_entry(self):
        import_thesaurus(_file([_entry(id="a", canonical="Термин А"), _entry(id="b", canonical="Термин Б")]))
        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.THESAURUS_UPDATED)
        self.assertEqual(entries.count(), 1)
        details = entries.first().details
        self.assertEqual(sorted(details["created"]), ["a", "b"])

    def test_noop_reimport_does_not_write_audit_entry(self):
        entries = [_entry(id="a", canonical="Термин А")]
        import_thesaurus(_file(entries))
        count_before = AuditLog.objects.filter(event_type=AuditLog.EventType.THESAURUS_UPDATED).count()
        import_thesaurus(_file(entries))
        count_after = AuditLog.objects.filter(event_type=AuditLog.EventType.THESAURUS_UPDATED).count()
        self.assertEqual(count_before, count_after)

    def test_audit_entry_includes_diff_for_updates(self):
        import_thesaurus(_file([_entry(id="a", canonical="Старое")]))
        import_thesaurus(_file([_entry(id="a", canonical="Новое")]))
        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.THESAURUS_UPDATED).order_by("created_at")
        last = entries.last()
        self.assertEqual(last.details["updated"], ["a"])
        self.assertEqual(last.details["diffs"]["a"]["canonical"], ["Старое", "Новое"])


class ImportThesaurusFileLevelWarningsTests(TestCase):
    def test_short_form_colliding_with_unregistered_canonical_warns(self):
        # short_form "ТП" записи "a" совпадает с canonical записи "b" — и
        # эта пара нигде не зарегистрирована в ambiguity_registry.
        report = import_thesaurus(_file([
            _entry(id="a", canonical="Тяговая подстанция", short_forms=["ТП"]),
            _entry(id="b", canonical="ТП", short_forms=["ТП-1"]),
        ]))
        self.assertTrue(any("TH-02" in w for w in report.warnings))

    def test_registered_ambiguity_does_not_warn(self):
        report = import_thesaurus(_file(
            [
                _entry(id="a", canonical="Тяговая подстанция", short_forms=["ТП"]),
                _entry(id="b", canonical="ТП", short_forms=["ТП-1"]),
            ],
            ambiguity_registry=[{"abbr": "ТП", "candidates": []}],
        ))
        self.assertFalse(any("TH-02" in w for w in report.warnings))

    def test_small_dataset_warns_th08(self):
        report = import_thesaurus(_file([_entry(id="a", canonical="Единственный термин")]))
        self.assertTrue(any("TH-08" in w for w in report.warnings))
