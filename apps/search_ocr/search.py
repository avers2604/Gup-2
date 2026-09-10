"""
Поисковый движок Smart Search (ТЗ 4.4.1): расширение запроса по
тезаурусу (`expand_query`) + полнотекстовый поиск по карточкам НРД
(`search_documents`), ранжирование — дословно формула ТЗ 2.2 §4.4.1:

    score = ExactMatch(reg_number) * 1.0
          + FTS(title)             * 0.8
          + FTS(summary)           * 0.5
          + FTS(ocr_body)          * 0.2

Расширение по тезаурусу — НЕ отдельное слагаемое формулы, а подмешивается
ВНУТРЬ каждого компонента: список терминов поиска = [сам запрос
пользователя (вес 1.0)] + expand_query(запрос) (веса по TH-06 —
short_forms/synonyms дают entry.weight, synonyms_legacy — фиксированные
0.3, неоднозначные аббревиатуры — candidates[].weight или 1.0 при
совпадении факультативного фасета категория/служба). Каждый термин
проверяется на точное совпадение с reg_number (вклад в ExactMatch) и
участвует в полнотекстовом поиске по каждому из title/summary/ocr_body
(вклад термин.weight * SearchRank, просуммированный по всем терминам,
затем домноженный на вес соответствующего поля).
"""
import re
from dataclasses import dataclass

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db.models import Case, ExpressionWrapper, F, FloatField, QuerySet, Value, When
from django.db.models.functions import Greatest

from apps.documents.models import NormativeDocument

from .models import ThesaurusEntry, ThesaurusStatus
from .normalization import normalize_term

# Веса компонентов формулы — дословно ТЗ 2.2 §4.4.1.
EXACT_MATCH_WEIGHT = 1.0
TITLE_WEIGHT = 0.8
SUMMARY_WEIGHT = 0.5
OCR_BODY_WEIGHT = 0.2

# TH-06 (validation_rules файла тезауруса) дословно: "synonyms_legacy
# расширяют запрос только с весом 0.3" — фиксированное значение, а не
# entry.weight * 0.3.
LEGACY_SYNONYM_WEIGHT = 0.3

# Верхняя граница числа терминов расширения на один запрос — каждый
# термин даёт 1 (ExactMatch) + 3 (title/summary/ocr_body) SQL-аннотации;
# без границы запрос, совпадающий с большим числом записей тезауруса, дал
# бы неограниченно тяжёлый SQL-запрос на один HTTP-запрос пользователя.
# Ниже, чем было до появления трёх FTS-полей вместо одного общего вектора
# (было 8) — та же осторожность, но с поправкой на утроившуюся цену
# одного термина.
MAX_EXPANDED_TERMS = 5
SEARCH_CONFIG = "russian"


@dataclass(frozen=True)
class ExpandedTerm:
    text: str
    weight: float
    source_entry_id: str
    via: str  # "short_form" | "synonym" | "synonym_legacy" | "canonical" | "direct"


def _contains_term(normalized_query: str, term: str) -> bool:
    """Термин присутствует в запросе как целое слово/фраза, а не как
    подстрока внутри другого слова (иначе short_form «ТО» ложно совпал бы
    с «авТОбус»). Границы (?<!\\w)/(?!\\w) — не \\b: \\b считает дефис
    словообразующим неоднозначно на обеих сторонах, здесь важна граница
    именно по буквенно-цифровым символам, включая случаи вроде «149-фз»
    внутри «149-фз.»."""
    normalized_term = normalize_term(term)
    if not normalized_term:
        return False
    pattern = r"(?<!\w)" + re.escape(normalized_term) + r"(?!\w)"
    return re.search(pattern, normalized_query) is not None


def expand_query(
    raw_query: str, *, category: str | None = None, service: str | None = None,
) -> list[ExpandedTerm]:
    """Расширение запроса по тезаурусу. Использует ТОЛЬКО записи со
    status=VERIFIED (TH-07 — verified проставляется вручную Контролёром/
    Юристом или Администратором, см. ThesaurusEntryAdmin) — неподтверждённые
    (draft) или отклонённые (rejected) записи не должны незаметно менять
    результаты поиска для всех пользователей.

    category/service — опциональные фасеты для разрешения неоднозначных
    аббревиатур (ThesaurusAmbiguity/ThesaurusAmbiguityCandidate): если
    совпадают с полями .category/.service самого́ кандидата-записи,
    кандидат получает вес 1.0 (однозначно выбран), иначе используется его
    собственный candidate.weight из ambiguity_registry (структурные
    данные — не парсинг свободного текста disambiguation, см. докстринг
    ThesaurusAmbiguity)."""
    normalized_query = normalize_term(raw_query)
    if not normalized_query:
        return []

    matches: dict[str, ExpandedTerm] = {}  # canonical -> лучший найденный ExpandedTerm

    verified_entries = ThesaurusEntry.objects.filter(status=ThesaurusStatus.VERIFIED).prefetch_related(
        "ambiguity_candidates"
    )
    for entry in verified_entries:
        via = None
        for sf in entry.short_forms:
            if _contains_term(normalized_query, sf):
                via = "short_form"
                break
        if via is None:
            for syn in entry.synonyms:
                if _contains_term(normalized_query, syn):
                    via = "synonym"
                    break
        legacy_match = False
        if via is None:
            for legacy in entry.synonyms_legacy:
                if _contains_term(normalized_query, legacy):
                    via = "synonym_legacy"
                    legacy_match = True
                    break
        if via is None and _contains_term(normalized_query, entry.canonical):
            via = "canonical"

        if via is None:
            continue

        weight = LEGACY_SYNONYM_WEIGHT if legacy_match else entry.weight

        # ambiguity_candidates уже prefetch'нут выше — обращение к .all()
        # здесь не даёт дополнительного запроса к БД.
        candidates = list(entry.ambiguity_candidates.all())
        if candidates:
            # На практике у записи не больше одного кандидата на одну
            # аббревиатуру (одна запись = одно значение в реестре).
            candidate = candidates[0]
            facet_matched = bool(
                (category and entry.category == category) or (service and entry.service == service)
            )
            weight = 1.0 if facet_matched else candidate.weight

        # Канонической формой расширяем поиск (не самим совпавшим
        # short_form/synonym — при вводе «ТП» искать по документам нужно
        # «тяговая подстанция», а не заново по «ТП», это уже часть
        # исходного запроса).
        canonical = entry.canonical
        existing = matches.get(canonical)
        if existing is None or weight > existing.weight:
            matches[canonical] = ExpandedTerm(
                text=canonical, weight=weight, source_entry_id=entry.id, via=via,
            )

    ordered = sorted(matches.values(), key=lambda t: t.weight, reverse=True)
    return ordered[:MAX_EXPANDED_TERMS]


def search_documents(
    user, raw_query: str, *, category: str | None = None, service: str | None = None,
) -> QuerySet:
    """Полнотекстовый поиск по карточкам НРД — формула ранжирования
    дословно ТЗ 2.2 §4.4.1 (см. докстринг модуля). Возвращает QuerySet
    NormativeDocument с аннотацией .score, отсортированный по убыванию;
    документы без единого совпадения (score <= 0) не включены.

    Доступ к ДСП: документы с access_level=RESTRICTED видны только
    пользователям с dsp_access=True или суперпользователям — то же
    правило, что и в NormativeDocumentAdmin.get_queryset(), продублировано
    здесь осознанно: поиск — самостоятельная точка входа к тем же данным,
    не должна полагаться на проверку допуска где-то выше по стеку вызовов.

    Черновики (status=DRAFT) исключены из результатов — САМОСТОЯТЕЛЬНОЕ,
    НЕ подтверждённое Заказчиком допущение («база знаний» ищет то, что
    когда-либо было официально зарегистрировано, не рабочие черновики).
    Кому именно, помимо общего исключения, должны быть видны черновики в
    поиске (матрица доступа по ролям) — нигде не специфицировано: роль
    «Куратор», которая упоминалась в более ранних формулировках такого
    правила, в проекте упразднена (см. STACK.md), замены ей для этого
    конкретного случая Заказчик не называл — оставлено как есть, а не
    придумано самостоятельно."""
    normalized_query = normalize_term(raw_query)
    if not normalized_query:
        return NormativeDocument.objects.none()

    queryset = NormativeDocument.objects.exclude(status=NormativeDocument.Status.DRAFT)
    if not (getattr(user, "is_authenticated", False) and (user.is_superuser or user.dsp_access)):
        queryset = queryset.filter(access_level=NormativeDocument.AccessLevel.GENERAL)

    # Термины поиска: сам запрос (вес 1.0, websearch_to_tsquery — свободный
    # пользовательский текст) + расширение по тезаурусу (веса по TH-06,
    # phraseto_tsquery — устойчивые словосочетания из canonical).
    direct_term = ExpandedTerm(text=raw_query.strip(), weight=1.0, source_entry_id="", via="direct")
    all_terms = [direct_term] + expand_query(raw_query, category=category, service=service)

    # 1. ExactMatch(reg_number) * 1.0 — точное совпадение (регистронезависимо)
    # с любым из терминов, максимум по всем терминам. На практике обычно
    # срабатывает только на прямом запросе (термины расширения — фразы из
    # тезауруса, не номера документов), но формула проверяется единообразно
    # для всех терминов, не только для прямого запроса.
    exact_terms = []
    for i, term in enumerate(all_terms):
        field_name = f"exact_{i}"
        queryset = queryset.annotate(**{field_name: Case(
            When(reg_number__iexact=term.text, then=Value(term.weight * EXACT_MATCH_WEIGHT)),
            default=Value(0.0),
            output_field=FloatField(),
        )})
        exact_terms.append(F(field_name))
    exact_score = Greatest(*exact_terms) if len(exact_terms) > 1 else exact_terms[0]

    score = exact_score

    # 2-4. FTS(title)*0.8 + FTS(summary)*0.5 + FTS(ocr_body)*0.2 — КАЖДОЕ
    # поле своим SearchVector (не общий вектор с внутренними весами A/B/C/D
    # Postgres: веса полей здесь берутся из самой формулы ТЗ). Каждый
    # термин — отдельная SearchRank-аннотация: ts_rank не позволяет
    # взвесить отдельные лексемы внутри одного tsquery, поэтому вклад
    # термин.weight * ts_rank(термин) суммируется по терминам вручную.
    for field_name, field_weight in (
        ("title", TITLE_WEIGHT), ("summary", SUMMARY_WEIGHT), ("ocr_body", OCR_BODY_WEIGHT),
    ):
        field_vector = SearchVector(field_name, config=SEARCH_CONFIG)
        field_terms = []
        for i, term in enumerate(all_terms):
            rank_field = f"{field_name}_rank_{i}"
            # phrase — термины расширения это устойчивые словосочетания
            # тезауруса (canonical), не свободный пользовательский текст:
            # нужно совпадение как фразы целиком, а не AND отдельных слов
            # где угодно в документе. Прямой запрос — websearch, обычный
            # пользовательский свободный текст.
            search_type = "websearch" if term.via == "direct" else "phrase"
            term_query = SearchQuery(term.text, config=SEARCH_CONFIG, search_type=search_type)
            queryset = queryset.annotate(**{rank_field: SearchRank(field_vector, term_query)})
            field_terms.append(F(rank_field) * term.weight)
        field_score = field_terms[0]
        for extra in field_terms[1:]:
            field_score = field_score + extra
        score = score + field_score * field_weight

    queryset = queryset.annotate(
        score=ExpressionWrapper(score, output_field=FloatField()),
    ).filter(score__gt=0).order_by("-score", "-reg_date")
    return queryset
