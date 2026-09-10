import io
import json

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.audit.models import AuditLog

from ..models import ThesaurusAmbiguity, ThesaurusAmbiguityCandidate, ThesaurusEntry
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

    def test_oversized_file_rejected_before_json_parsing(self):
        with self.assertRaisesMessage(ValidationError, "слишком большой"):
            import_thesaurus(io.BytesIO(b"{" + b"x" * (10 * 1024 * 1024) + b"}"))

    def test_too_many_entries_rejected(self):
        entries = [_entry(id=f"entry-{index}", canonical=f"Термин {index}") for index in range(10_001)]
        with self.assertRaisesMessage(ValidationError, "слишком много записей"):
            import_thesaurus(_file(entries))

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

    def test_close_short_forms_warn_th03(self):
        # "149-фз" и "152-фз" — расстояние Левенштейна 2, оба длиннее
        # порога (>=5 символов) — реальный риск спутать номер закона.
        report = import_thesaurus(_file([
            _entry(id="a", canonical="ФЗ О персональных данных", short_forms=["149-фз"]),
            _entry(id="b", canonical="ФЗ О безопасности движения", short_forms=["152-фз"]),
        ]))
        self.assertTrue(any("TH-03" in w for w in report.warnings))

    def test_registered_th03_pair_does_not_warn(self):
        report = import_thesaurus(_file(
            [
                _entry(id="a", canonical="ФЗ О персональных данных", short_forms=["149-фз"]),
                _entry(id="b", canonical="ФЗ О безопасности движения", short_forms=["152-фз"]),
            ],
            ambiguity_registry=[{"abbr": "149-фз", "candidates": []}],
        ))
        self.assertFalse(any("TH-03" in w for w in report.warnings))

    def test_short_abbreviations_below_length_threshold_do_not_warn_th03(self):
        # Короткие формы (<5 символов) намеренно исключены из TH-03 — см.
        # комментарий в _check_file_level_rules: расстояние <=2 для них
        # почти ничего не отсекает и превратило бы предупреждение в шум.
        report = import_thesaurus(_file([
            _entry(id="a", canonical="Термин А", short_forms=["ГИ"]),
            _entry(id="b", canonical="Термин Б", short_forms=["ОК"]),
        ]))
        self.assertFalse(any("TH-03" in w for w in report.warnings))


class ImportThesaurusAmbiguityPersistenceTests(TestCase):
    """ambiguity_registry файла теперь персистится (ThesaurusAmbiguity +
    ThesaurusAmbiguityCandidate — настоящий FK на ThesaurusEntry, не
    JSONField), не только используется транзитно для warnings TH-02/03."""

    def test_ambiguity_registry_creates_ambiguity_and_candidates(self):
        import_thesaurus(_file(
            [
                _entry(id="tp_main", canonical="Тяговая подстанция", short_forms=["ТП"]),
                _entry(id="tp_incident", canonical="Транспортное происшествие", short_forms=["ТП-1"]),
            ],
            ambiguity_registry=[{
                "abbr": "ТП",
                "candidates": [
                    {"id": "tp_main", "weight": 1.0, "reason": "основное значение"},
                    {"id": "tp_incident", "weight": 0.6, "reason": "контекст инцидентов"},
                ],
            }],
        ))
        ambiguity = ThesaurusAmbiguity.objects.get(abbr="ТП")
        candidates = {c.entry_id: c.weight for c in ambiguity.candidates.all()}
        self.assertEqual(candidates, {"tp_main": 1.0, "tp_incident": 0.6})

    def test_candidate_has_real_fk_to_thesaurus_entry(self):
        import_thesaurus(_file(
            [_entry(id="tp_main", canonical="Тяговая подстанция", short_forms=["ТП"])],
            ambiguity_registry=[{"abbr": "ТП", "candidates": [{"id": "tp_main", "weight": 1.0}]}],
        ))
        candidate = ThesaurusAmbiguityCandidate.objects.get(ambiguity__abbr="ТП")
        self.assertEqual(candidate.entry, ThesaurusEntry.objects.get(pk="tp_main"))

    def test_candidate_referencing_missing_entry_warns_not_fails(self):
        report = import_thesaurus(_file(
            [_entry(id="tp_main", canonical="Тяговая подстанция", short_forms=["ТП"])],
            ambiguity_registry=[{
                "abbr": "ТП",
                "candidates": [
                    {"id": "tp_main", "weight": 1.0},
                    {"id": "does_not_exist", "weight": 0.5},
                ],
            }],
        ))
        self.assertTrue(any("does_not_exist" in w for w in report.warnings))
        self.assertEqual(ThesaurusAmbiguityCandidate.objects.count(), 1)

    def test_reimport_with_changed_weight_updates_candidate(self):
        base_entries = [_entry(id="tp_main", canonical="Тяговая подстанция", short_forms=["ТП"])]
        import_thesaurus(_file(
            base_entries, ambiguity_registry=[{"abbr": "ТП", "candidates": [{"id": "tp_main", "weight": 1.0}]}],
        ))
        import_thesaurus(_file(
            base_entries, ambiguity_registry=[{"abbr": "ТП", "candidates": [{"id": "tp_main", "weight": 0.7}]}],
        ))
        candidate = ThesaurusAmbiguityCandidate.objects.get(ambiguity__abbr="ТП", entry_id="tp_main")
        self.assertEqual(candidate.weight, 0.7)
        self.assertEqual(ThesaurusAmbiguityCandidate.objects.count(), 1)

    def test_reimport_removing_candidate_deletes_stale_row(self):
        entries = [
            _entry(id="tp_main", canonical="Тяговая подстанция", short_forms=["ТП"]),
            _entry(id="tp_incident", canonical="Транспортное происшествие", short_forms=["ТП-1"]),
        ]
        import_thesaurus(_file(entries, ambiguity_registry=[{
            "abbr": "ТП",
            "candidates": [{"id": "tp_main", "weight": 1.0}, {"id": "tp_incident", "weight": 0.6}],
        }]))
        self.assertEqual(ThesaurusAmbiguityCandidate.objects.filter(ambiguity__abbr="ТП").count(), 2)

        import_thesaurus(_file(entries, ambiguity_registry=[{
            "abbr": "ТП", "candidates": [{"id": "tp_main", "weight": 1.0}],
        }]))
        remaining = ThesaurusAmbiguityCandidate.objects.filter(ambiguity__abbr="ТП")
        self.assertEqual(remaining.count(), 1)
        self.assertEqual(remaining.first().entry_id, "tp_main")

    def test_reimport_unchanged_ambiguity_writes_no_extra_audit_entry(self):
        entries = [_entry(id="tp_main", canonical="Тяговая подстанция", short_forms=["ТП"])]
        registry = [{"abbr": "ТП", "candidates": [{"id": "tp_main", "weight": 1.0}]}]
        import_thesaurus(_file(entries, ambiguity_registry=registry))
        report = import_thesaurus(_file(entries, ambiguity_registry=registry))
        self.assertEqual(report.ambiguity_updated, [])
        self.assertEqual(
            AuditLog.objects.filter(event_type=AuditLog.EventType.THESAURUS_UPDATED).count(), 1,
        )
