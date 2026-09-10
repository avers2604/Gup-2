"""apps/search_ocr/search.py — расширение запроса (expand_query) и
полнотекстовый поиск по НРД (search_documents), ТЗ 4.4.1."""
import datetime

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.documents.models import NormativeDocument
from apps.documents.retention import RetentionCategory
from apps.documents.tests.factories import make_document
from apps.iam.models import Department, User

from ..models import (
    ThesaurusAmbiguity,
    ThesaurusAmbiguityCandidate,
    ThesaurusCategory,
    ThesaurusEntry,
    ThesaurusService,
    ThesaurusStatus,
)
from ..search import expand_query, search_documents


def _make_entry(entry_id, canonical, *, status=ThesaurusStatus.VERIFIED, **kwargs):
    defaults = dict(
        canonical=canonical, category=ThesaurusCategory.TECH_ENERGY, service=None,
        short_forms=[], synonyms=[], synonyms_legacy=[], weight=1.0, status=status,
    )
    defaults.update(kwargs)
    return ThesaurusEntry.objects.create(id=entry_id, **defaults)


def _make_ambiguity(abbr, candidates, disambiguation=""):
    """candidates: iterable of (entry_id, weight) tuples."""
    ambiguity = ThesaurusAmbiguity.objects.create(abbr=abbr, disambiguation=disambiguation)
    for entry_id, weight in candidates:
        ThesaurusAmbiguityCandidate.objects.create(
            ambiguity=ambiguity, entry=ThesaurusEntry.objects.get(pk=entry_id), weight=weight,
        )
    return ambiguity


def _make_user(role=User.Role.READER, personnel_number="0001", **kwargs):
    dept, _ = Department.objects.get_or_create(
        name="Служба движения", defaults={"level": Department.Level.SERVICE}
    )
    defaults = dict(
        personnel_number=personnel_number, last_name="Иванов", first_name="Пётр",
        position="Водитель", department=dept, role=role,
    )
    defaults.update(kwargs)
    return User.objects.create(**defaults)


class ExpandQueryTests(TestCase):
    def test_exact_short_form_match_returns_canonical_with_entry_weight(self):
        _make_entry("tp", "тяговая подстанция", short_forms=["ТП"], weight=1.0)
        terms = expand_query("авария на ТП вчера")
        self.assertEqual(len(terms), 1)
        self.assertEqual(terms[0].text, "тяговая подстанция")
        self.assertEqual(terms[0].weight, 1.0)
        self.assertEqual(terms[0].via, "short_form")

    def test_draft_entry_not_used_for_expansion(self):
        _make_entry("tp", "тяговая подстанция", short_forms=["ТП"], status=ThesaurusStatus.DRAFT)
        self.assertEqual(expand_query("ТП"), [])

    def test_rejected_entry_not_used_for_expansion(self):
        _make_entry("tp", "тяговая подстанция", short_forms=["ТП"], status=ThesaurusStatus.REJECTED)
        self.assertEqual(expand_query("ТП"), [])

    def test_synonym_legacy_uses_fixed_weight_regardless_of_entry_weight(self):
        # TH-06: "synonyms_legacy расширяют запрос только с весом 0.3" —
        # дословно фиксированное значение, а НЕ entry.weight * 0.3.
        # TH-04 требует хотя бы один short_form/synonym — «ТМ» здесь просто
        # для валидности записи, в самом тесте не участвует.
        _make_entry("tp", "трамвайный парк", short_forms=["ТМ"], synonyms_legacy=["ТП-1"], weight=0.8)
        terms = expand_query("состав из ТП-1")
        self.assertEqual(len(terms), 1)
        self.assertEqual(terms[0].weight, 0.3)
        self.assertEqual(terms[0].via, "synonym_legacy")

    def test_synonym_match_uses_entry_weight(self):
        _make_entry("director", "директор предприятия", synonyms=["руководитель предприятия"], weight=0.9)
        terms = expand_query("приказ руководитель предприятия подписал")
        self.assertEqual(len(terms), 1)
        self.assertEqual(terms[0].weight, 0.9)
        self.assertEqual(terms[0].via, "synonym")

    def test_canonical_itself_matches(self):
        _make_entry("tp", "тяговая подстанция", short_forms=["ТП"])
        terms = expand_query("ремонт тяговая подстанция номер три")
        self.assertEqual(len(terms), 1)
        self.assertEqual(terms[0].via, "canonical")

    def test_no_match_returns_empty_list(self):
        _make_entry("tp", "тяговая подстанция", short_forms=["ТП"])
        self.assertEqual(expand_query("совершенно не связанный запрос"), [])

    def test_empty_query_returns_empty_list(self):
        self.assertEqual(expand_query(""), [])
        self.assertEqual(expand_query("   "), [])

    def test_short_form_does_not_match_as_substring_of_another_word(self):
        # short_form «ТО» не должен «находиться» внутри «авто».
        _make_entry("to", "технический осмотр", short_forms=["ТО"])
        self.assertEqual(expand_query("купил новое авто"), [])

    def test_multi_word_canonical_matched_as_phrase_not_loose_words(self):
        _make_entry("tp", "тяговая подстанция", short_forms=["ТП"])
        # Слова встречаются в запросе, но не как последовательная фраза —
        # не должно считаться совпадением по canonical (только по ТП).
        terms = expand_query("подстанция была тяговая")
        self.assertEqual(terms, [])

    def test_ambiguous_abbreviation_without_facet_uses_candidate_weight(self):
        _make_entry("tp_main", "тяговая подстанция", short_forms=["ТП"], weight=1.0,
                     category=ThesaurusCategory.TECH_ENERGY, service=ThesaurusService.EKH)
        _make_entry("tp_incident", "транспортное происшествие", short_forms=["ТП"], weight=1.0,
                     category=ThesaurusCategory.INCIDENT, service=ThesaurusService.SD)
        _make_ambiguity("ТП", [("tp_main", 1.0), ("tp_incident", 0.6)])
        terms = {t.source_entry_id: t.weight for t in expand_query("акт по ТП")}
        self.assertEqual(terms["tp_main"], 1.0)
        self.assertEqual(terms["tp_incident"], 0.6)

    def test_ambiguous_abbreviation_with_matching_category_facet_gets_full_weight(self):
        _make_entry("tp_main", "тяговая подстанция", short_forms=["ТП"], weight=1.0,
                     category=ThesaurusCategory.TECH_ENERGY, service=ThesaurusService.EKH)
        _make_entry("tp_incident", "транспортное происшествие", short_forms=["ТП"], weight=1.0,
                     category=ThesaurusCategory.INCIDENT, service=ThesaurusService.SD)
        _make_ambiguity("ТП", [("tp_main", 1.0), ("tp_incident", 0.6)])
        # Фасета совпадает именно с category=incident у tp_incident — тот
        # получает полный вес, tp_main остаётся на своём candidate weight.
        terms = {
            t.source_entry_id: t.weight
            for t in expand_query("акт по ТП", category=ThesaurusCategory.INCIDENT)
        }
        self.assertEqual(terms["tp_incident"], 1.0)
        self.assertEqual(terms["tp_main"], 1.0)  # у tp_main свой candidate weight и так 1.0

    def test_tp_service_ekh_facet_selects_tyagovaya_podstanciya(self):
        # Буквальный пример из ambiguity_registry файла: «ТП» + служба ЭХ
        # -> тяговая подстанция (полный вес), другой кандидат — на своём.
        _make_entry("tp_main", "тяговая подстанция", short_forms=["ТП"], weight=1.0,
                     category=ThesaurusCategory.TECH_ENERGY, service=ThesaurusService.EKH)
        _make_entry("tp_incident", "транспортное происшествие", short_forms=["ТП"], weight=1.0,
                     category=ThesaurusCategory.INCIDENT, service=ThesaurusService.SD)
        _make_ambiguity("ТП", [("tp_main", 1.0), ("tp_incident", 0.6)])
        terms = {
            t.source_entry_id: t.weight
            for t in expand_query("акт по ТП", service=ThesaurusService.EKH)
        }
        self.assertEqual(terms["tp_main"], 1.0)
        self.assertEqual(terms["tp_incident"], 0.6)

    def test_tp_service_movement_facet_selects_incident(self):
        # «ТП» + служба движения -> транспортное происшествие получает
        # полный вес (фасета совпала с его .service), не тяговая подстанция.
        _make_entry("tp_main", "тяговая подстанция", short_forms=["ТП"], weight=1.0,
                     category=ThesaurusCategory.TECH_ENERGY, service=ThesaurusService.EKH)
        _make_entry("tp_incident", "транспортное происшествие", short_forms=["ТП"], weight=1.0,
                     category=ThesaurusCategory.INCIDENT, service=ThesaurusService.SD)
        _make_ambiguity("ТП", [("tp_main", 1.0), ("tp_incident", 0.6)])
        terms = {
            t.source_entry_id: t.weight
            for t in expand_query("акт по ТП", service=ThesaurusService.SD)
        }
        self.assertEqual(terms["tp_incident"], 1.0)
        self.assertEqual(terms["tp_main"], 1.0)  # у tp_main свой candidate weight и так 1.0

    def test_to_without_facet_uses_candidate_weight(self):
        _make_entry("to_general", "техническое обслуживание", short_forms=["ТО"], weight=1.0,
                     category=ThesaurusCategory.PROCESS, service=None)
        _make_entry("to_inspection", "технический осмотр", short_forms=["ТО"], weight=1.0,
                     category=ThesaurusCategory.INCIDENT, service=None)
        _make_ambiguity("ТО", [("to_general", 1.0), ("to_inspection", 0.6)])
        terms = {t.source_entry_id: t.weight for t in expand_query("плановое ТО")}
        self.assertEqual(terms["to_general"], 1.0)
        self.assertEqual(terms["to_inspection"], 0.6)

    def test_to_with_category_facet_selects_inspection(self):
        _make_entry("to_general", "техническое обслуживание", short_forms=["ТО"], weight=1.0,
                     category=ThesaurusCategory.PROCESS, service=None)
        _make_entry("to_inspection", "технический осмотр", short_forms=["ТО"], weight=1.0,
                     category=ThesaurusCategory.INCIDENT, service=None)
        _make_ambiguity("ТО", [("to_general", 1.0), ("to_inspection", 0.6)])
        terms = {
            t.source_entry_id: t.weight
            for t in expand_query("плановое ТО", category=ThesaurusCategory.INCIDENT)
        }
        self.assertEqual(terms["to_inspection"], 1.0)
        self.assertEqual(terms["to_general"], 1.0)  # у to_general и так candidate weight=1.0

    def test_expansion_capped_at_max_terms(self):
        from .. import search as search_module

        for i in range(search_module.MAX_EXPANDED_TERMS + 5):
            _make_entry(f"e{i}", f"термин номер {i}", short_forms=[f"ТЕРМИН{i}"], weight=1.0)
        query = " ".join(f"ТЕРМИН{i}" for i in range(search_module.MAX_EXPANDED_TERMS + 5))
        terms = expand_query(query)
        self.assertLessEqual(len(terms), search_module.MAX_EXPANDED_TERMS)


class SearchDocumentsTests(TestCase):
    def setUp(self):
        self.reader = _make_user(User.Role.READER, personnel_number="0001")
        self.dsp_reader = _make_user(
            User.Role.READER, personnel_number="0002", dsp_access=True,
        )
        self.superuser = _make_user(
            User.Role.ADMINISTRATOR, personnel_number="0003", is_superuser=True,
        )

    def test_direct_match_found(self):
        make_document(
            reg_number="1-п", title="Приказ о тяговых подстанциях", status=NormativeDocument.Status.ACTIVE,
        )
        results = list(search_documents(self.reader, "тяговых подстанциях"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].reg_number, "1-п")

    def test_no_match_returns_empty(self):
        make_document(reg_number="1-п", title="Приказ о тяговых подстанциях")
        results = list(search_documents(self.reader, "совершенно другая тема"))
        self.assertEqual(results, [])

    def test_empty_query_returns_empty(self):
        make_document(reg_number="1-п", title="Приказ о тяговых подстанциях")
        results = list(search_documents(self.reader, ""))
        self.assertEqual(results, [])

    def test_draft_document_excluded(self):
        make_document(
            reg_number="1-п", title="Приказ о тяговых подстанциях", status=NormativeDocument.Status.DRAFT,
        )
        results = list(search_documents(self.reader, "тяговых подстанциях"))
        self.assertEqual(results, [])

    def test_archived_document_included(self):
        make_document(
            reg_number="1-п", title="Приказ о тяговых подстанциях", status=NormativeDocument.Status.ARCHIVED,
        )
        results = list(search_documents(self.reader, "тяговых подстанциях"))
        self.assertEqual(len(results), 1)

    def test_dsp_document_hidden_from_reader_without_access(self):
        make_document(
            reg_number="1-дсп", title="Секретный приказ о тяговых подстанциях",
            status=NormativeDocument.Status.ACTIVE, access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        results = list(search_documents(self.reader, "тяговых подстанциях"))
        self.assertEqual(results, [])

    def test_dsp_document_visible_to_user_with_dsp_access(self):
        make_document(
            reg_number="1-дсп", title="Секретный приказ о тяговых подстанциях",
            status=NormativeDocument.Status.ACTIVE, access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        results = list(search_documents(self.dsp_reader, "тяговых подстанциях"))
        self.assertEqual(len(results), 1)

    def test_dsp_document_visible_to_superuser(self):
        make_document(
            reg_number="1-дсп", title="Секретный приказ о тяговых подстанциях",
            status=NormativeDocument.Status.ACTIVE, access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        results = list(search_documents(self.superuser, "тяговых подстанциях"))
        self.assertEqual(len(results), 1)

    def test_abbreviation_query_finds_document_only_via_expansion(self):
        # expansion_doc не упоминает саму аббревиатуру «ТП» нигде — находится
        # ТОЛЬКО через расширение запроса по тезаурусу (canonical-форма в
        # summary). Сравнение "прямое совпадение сильнее расширения" здесь
        # намеренно не проверяется как общее свойство формулы: при разных
        # полях/структуре совпадения (однословная аббревиатура в title vs
        # двухсловная фраза в summary) raw ts_rank Postgres не гарантирует
        # такой порядок (у фразового совпадения больше значащих лексем) —
        # соответствующее свойство (текущее обозначение весомее legacy)
        # проверяется на равных условиях в RankingFormulaTests
        # (test_synonyms_legacy_contribute_reduced_weight_to_score).
        _make_entry("tp", "тяговая подстанция", short_forms=["ТП"], weight=1.0)
        expansion_doc = make_document(
            reg_number="2-п", title="Общий регламент",
            summary="Работа тяговая подстанция описана отдельно", status=NormativeDocument.Status.ACTIVE,
        )
        results = list(search_documents(self.reader, "ТП"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].reg_number, expansion_doc.reg_number)
        self.assertGreater(results[0].score, 0)

    def test_general_document_visible_to_reader_without_dsp_access(self):
        # access_level=GENERAL (умолчание make_document) — «Общий» гриф,
        # виден любому вошедшему пользователю независимо от dsp_access.
        make_document(
            reg_number="1-п", title="Общедоступный приказ о тяговых подстанциях",
            status=NormativeDocument.Status.ACTIVE, access_level=NormativeDocument.AccessLevel.GENERAL,
        )
        results = list(search_documents(self.reader, "тяговых подстанциях"))
        self.assertEqual(len(results), 1)


class RankingFormulaTests(TestCase):
    """Формула ранжирования — дословно ТЗ 2.2 §4.4.1:
    score = ExactMatch(reg_number)*1.0 + FTS(title)*0.8 + FTS(summary)*0.5
    + FTS(ocr_body)*0.2."""

    def setUp(self):
        self.reader = _make_user(User.Role.READER, personnel_number="0001")

    def test_exact_reg_number_match_ranks_above_plain_text_match(self):
        exact_doc = make_document(
            reg_number="142-п", title="Ничем не примечательный документ",
            status=NormativeDocument.Status.ACTIVE,
        )
        text_doc = make_document(
            reg_number="99-п", title="Обсуждение номера 142-п в другом документе",
            status=NormativeDocument.Status.ACTIVE,
        )
        results = list(search_documents(self.reader, "142-п"))
        reg_numbers = [r.reg_number for r in results]
        self.assertEqual(reg_numbers[0], exact_doc.reg_number)
        self.assertIn(text_doc.reg_number, reg_numbers)
        self.assertGreater(results[0].score, results[1].score)
        # ExactMatch даёт ровно 1.0 (EXACT_MATCH_WEIGHT * вес прямого
        # термина 1.0) поверх любого вклада FTS по тому же запросу.
        self.assertGreaterEqual(results[0].score, 1.0)

    def test_title_match_ranks_above_ocr_body_match_for_equivalent_text(self):
        title_doc = make_document(
            reg_number="1-п", title="Регламент по эксплуатации трамвайных путей",
            status=NormativeDocument.Status.ACTIVE,
        )
        ocr_doc = make_document(
            reg_number="2-п", title="Прочий документ", ocr_body="Регламент по эксплуатации трамвайных путей",
            status=NormativeDocument.Status.ACTIVE,
        )
        results = list(search_documents(self.reader, "эксплуатации трамвайных путей"))
        reg_numbers = [r.reg_number for r in results]
        self.assertEqual(reg_numbers[0], title_doc.reg_number)
        self.assertEqual(reg_numbers[1], ocr_doc.reg_number)
        self.assertGreater(results[0].score, results[1].score)

    def test_summary_match_ranks_above_ocr_body_match(self):
        summary_doc = make_document(
            reg_number="1-п", title="Общий документ", summary="Регламент по эксплуатации трамвайных путей",
            status=NormativeDocument.Status.ACTIVE,
        )
        ocr_doc = make_document(
            reg_number="2-п", title="Прочий документ", ocr_body="Регламент по эксплуатации трамвайных путей",
            status=NormativeDocument.Status.ACTIVE,
        )
        results = list(search_documents(self.reader, "эксплуатации трамвайных путей"))
        reg_numbers = [r.reg_number for r in results]
        self.assertEqual(reg_numbers[0], summary_doc.reg_number)
        self.assertEqual(reg_numbers[1], ocr_doc.reg_number)

    def test_synonyms_legacy_contribute_reduced_weight_to_score(self):
        # TH-06: synonyms_legacy расширяют запрос с фиксированным весом
        # 0.3 — документ, найденный ТОЛЬКО через legacy-обозначение,
        # должен получить меньший вклад в score, чем через актуальный
        # short_form/synonym (вес по умолчанию 1.0) при прочих равных.
        _make_entry(
            "tm1", "трамвайный парк № 1", short_forms=["ТМ-1"], synonyms_legacy=["ТП-1"], weight=1.0,
        )
        legacy_doc = make_document(
            reg_number="1-п", title="Общий регламент",
            summary="Работа трамвайный парк № 1 описана отдельно", status=NormativeDocument.Status.ACTIVE,
        )
        results_legacy = list(search_documents(self.reader, "ТП-1"))
        self.assertEqual(len(results_legacy), 1)
        self.assertEqual(results_legacy[0].reg_number, legacy_doc.reg_number)

        results_current = list(search_documents(self.reader, "ТМ-1"))
        self.assertEqual(len(results_current), 1)
        self.assertEqual(results_current[0].reg_number, legacy_doc.reg_number)

        # Legacy-обозначение («ТП-1», вес 0.3) даёт меньший score, чем
        # актуальное («ТМ-1», вес entry.weight=1.0), при совпадении с
        # одним и тем же документом через одно и то же поле (summary).
        self.assertLess(results_legacy[0].score, results_current[0].score)


class ThesaurusAmbiguityCandidateModelTests(TestCase):
    """ThesaurusAmbiguity/ThesaurusAmbiguityCandidate — настоящая связь с
    ThesaurusEntry (FK), не JSONField (см. докстринг моделей)."""

    def test_candidate_has_fk_to_thesaurus_entry(self):
        _make_entry("tp_main", "тяговая подстанция", short_forms=["ТП"])
        ambiguity = _make_ambiguity("ТП", [("tp_main", 1.0)])
        candidate = ambiguity.candidates.get()
        self.assertEqual(candidate.entry, ThesaurusEntry.objects.get(pk="tp_main"))
        # Обратная связь ThesaurusEntry -> кандидаты (используется
        # expand_query() через prefetch_related).
        entry = ThesaurusEntry.objects.get(pk="tp_main")
        self.assertEqual(list(entry.ambiguity_candidates.all()), [candidate])

    def test_duplicate_candidate_for_same_ambiguity_and_entry_rejected(self):
        _make_entry("tp_main", "тяговая подстанция", short_forms=["ТП"])
        ambiguity = _make_ambiguity("ТП", [("tp_main", 1.0)])
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ThesaurusAmbiguityCandidate.objects.create(
                    ambiguity=ambiguity, entry=ThesaurusEntry.objects.get(pk="tp_main"), weight=0.5,
                )

    def test_deleting_entry_cascades_to_candidate(self):
        _make_entry("tp_main", "тяговая подстанция", short_forms=["ТП"])
        ambiguity = _make_ambiguity("ТП", [("tp_main", 1.0)])
        ThesaurusEntry.objects.get(pk="tp_main").delete()
        self.assertFalse(ThesaurusAmbiguityCandidate.objects.filter(ambiguity=ambiguity).exists())

    def test_entry_field_is_indexed(self):
        # Индекс на entry — путь "запись тезауруса -> её неоднозначности",
        # которым пользуется expand_query() на каждый поисковый запрос.
        index_fields = [tuple(idx.fields) for idx in ThesaurusAmbiguityCandidate._meta.indexes]
        self.assertIn(("entry",), index_fields)
