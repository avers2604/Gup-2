"""apps/search_ocr/search.py — расширение запроса (expand_query) и
полнотекстовый поиск по НРД (search_documents), ТЗ 4.4.1."""
import datetime

from django.test import TestCase

from apps.documents.models import NormativeDocument
from apps.documents.retention import RetentionCategory
from apps.documents.tests.factories import make_document
from apps.iam.models import Department, User

from ..models import ThesaurusAmbiguity, ThesaurusCategory, ThesaurusEntry, ThesaurusService, ThesaurusStatus
from ..search import expand_query, search_documents


def _make_entry(entry_id, canonical, *, status=ThesaurusStatus.VERIFIED, **kwargs):
    defaults = dict(
        canonical=canonical, category=ThesaurusCategory.TECH_ENERGY, service=None,
        short_forms=[], synonyms=[], synonyms_legacy=[], weight=1.0, status=status,
    )
    defaults.update(kwargs)
    return ThesaurusEntry.objects.create(id=entry_id, **defaults)


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
                     category=ThesaurusCategory.INCIDENT, service=None)
        ThesaurusAmbiguity.objects.create(
            abbr="ТП",
            candidates=[
                {"id": "tp_main", "weight": 1.0},
                {"id": "tp_incident", "weight": 0.6},
            ],
        )
        terms = {t.source_entry_id: t.weight for t in expand_query("акт по ТП")}
        self.assertEqual(terms["tp_main"], 1.0)
        self.assertEqual(terms["tp_incident"], 0.6)

    def test_ambiguous_abbreviation_with_matching_facet_gets_full_weight(self):
        _make_entry("tp_main", "тяговая подстанция", short_forms=["ТП"], weight=1.0,
                     category=ThesaurusCategory.TECH_ENERGY, service=ThesaurusService.EKH)
        _make_entry("tp_incident", "транспортное происшествие", short_forms=["ТП"], weight=1.0,
                     category=ThesaurusCategory.INCIDENT, service=None)
        ThesaurusAmbiguity.objects.create(
            abbr="ТП",
            candidates=[
                {"id": "tp_main", "weight": 1.0},
                {"id": "tp_incident", "weight": 0.6},
            ],
        )
        # Фасета совпадает именно с category=incident у tp_incident — тот
        # получает полный вес, tp_main остаётся на своём candidate weight.
        terms = {
            t.source_entry_id: t.weight
            for t in expand_query("акт по ТП", category=ThesaurusCategory.INCIDENT)
        }
        self.assertEqual(terms["tp_incident"], 1.0)
        self.assertEqual(terms["tp_main"], 1.0)  # у tp_main свой candidate weight и так 1.0

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

    def test_direct_match_ranks_above_expansion_only_match(self):
        _make_entry("tp", "тяговая подстанция", short_forms=["ТП"], weight=1.0)
        # direct_doc содержит буквально запрос («ТП») — прямое совпадение.
        # expansion_doc содержит только каноническую форму («тяговая
        # подстанция»), саму аббревиатуру не упоминает — находится ТОЛЬКО
        # через расширение запроса по тезаурусу.
        direct_doc = make_document(
            reg_number="1-п", title="Регламент по ТП", status=NormativeDocument.Status.ACTIVE,
        )
        expansion_doc = make_document(
            reg_number="2-п", title="Общий регламент",
            summary="Работа тяговая подстанция описана отдельно", status=NormativeDocument.Status.ACTIVE,
        )
        results = list(search_documents(self.reader, "ТП"))
        reg_numbers = [r.reg_number for r in results]
        self.assertIn(direct_doc.reg_number, reg_numbers)
        self.assertIn(expansion_doc.reg_number, reg_numbers)
        self.assertEqual(reg_numbers[0], direct_doc.reg_number)
        self.assertGreater(results[0].score, results[1].score)
