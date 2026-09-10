"""
Поисковый движок Smart Search (ТЗ 4.4.1): расширение запроса по
тезаурусу (`expand_query`) + полнотекстовый поиск по карточкам НРД
(`search_documents`) с гибридным ранжированием (прямое совпадение +
расширенные термины).

ЧЕСТНАЯ ГРАНИЦА: файл тезауруса (docs/thesaurus/thesaurus_v0.9_draft.json)
несколько раз ссылается на «гибридную формулу ранжирования ТЗ 2.2 §4.4.1»
(например disambiguation записи «ТО» в ambiguity_registry), но буквальный
текст этой формулы в эту сессию не передавался. Формула ниже —
СОБСТВЕННАЯ реализация по духу доступных структурированных данных
(entry.weight, TH-06 вес synonyms_legacy=0.3, candidates[].weight у
неоднозначных аббревиатур), а не транскрипция текста ТЗ. Нужна сверка с
оригиналом при первой возможности — см. STACK.md, раздел про Smart Search.
"""
import re
from dataclasses import dataclass

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db.models import ExpressionWrapper, F, FloatField, QuerySet

from apps.documents.models import NormativeDocument

from .models import ThesaurusAmbiguity, ThesaurusEntry, ThesaurusStatus
from .normalization import normalize_term

# Константы гибридной формулы (см. честную границу выше) — не из ТЗ
# буквально, отдельно подобранные значения с понятным намерением: прямое
# совпадение запроса всегда весомее любого расширения по синониму (иначе
# документ, совпавший только по редкому синониму, мог бы обогнать
# документ с точным попаданием в запрос — контринтуитивно для поиска).
DIRECT_MATCH_WEIGHT = 1.0
EXPANSION_WEIGHT = 0.5
# TH-06 (validation_rules файла) дословно: "synonyms_legacy расширяют
# запрос только с весом 0.3" — фиксированное значение, а не entry.weight * 0.3.
LEGACY_SYNONYM_WEIGHT = 0.3
# Верхняя граница числа расширенных терминов на один запрос — каждый термин
# это отдельная SearchRank-аннотация (отдельный проход tsquery по
# вычисляемому на лету tsvector, см. search_documents) — без границы длинный
# запрос с множеством совпадений в тезаурусе превратился бы в вектор для
# перегрузки БД количеством аннотаций на один HTTP-запрос.
MAX_EXPANDED_TERMS = 8
SEARCH_CONFIG = "russian"


@dataclass(frozen=True)
class ExpandedTerm:
    text: str
    weight: float
    source_entry_id: str
    via: str  # "short_form" | "synonym" | "synonym_legacy" | "canonical"


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
    аббревиатур (ThesaurusAmbiguity): если совпадают с полями .category/
    .service самого́ кандидата-записи, кандидат получает вес 1.0
    (однозначно выбран), иначе используется его собственный
    candidates[].weight из ambiguity_registry (структурные данные — не
    парсинг свободного текста disambiguation, см. докстринг
    ThesaurusAmbiguity)."""
    normalized_query = normalize_term(raw_query)
    if not normalized_query:
        return []

    ambiguity_by_entry_id: dict[str, ThesaurusAmbiguity] = {}
    for amb in ThesaurusAmbiguity.objects.all():
        for cand in amb.candidates:
            entry_id = cand.get("id")
            if entry_id:
                ambiguity_by_entry_id.setdefault(entry_id, amb)

    matches: dict[str, ExpandedTerm] = {}  # canonical -> лучший найденный ExpandedTerm

    for entry in ThesaurusEntry.objects.filter(status=ThesaurusStatus.VERIFIED):
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

        ambiguity = ambiguity_by_entry_id.get(entry.id)
        if ambiguity is not None:
            facet_matched = bool(
                (category and entry.category == category) or (service and entry.service == service)
            )
            if facet_matched:
                weight = 1.0
            else:
                for cand in ambiguity.candidates:
                    if cand.get("id") == entry.id:
                        weight = cand.get("weight", weight)
                        break

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
    """Полнотекстовый поиск по карточкам НРД (ТЗ 4.4.1) — прямой запрос
    пользователя + термины расширения (expand_query), объединённые в один
    гибридный score (см. честную границу в докстринге модуля). Возвращает
    QuerySet NormativeDocument с аннотацией .score, отсортированный по
    убыванию; документы без единого совпадения (score <= 0) не включены.

    Доступ к ДСП: документы с access_level=RESTRICTED видны только
    пользователям с dsp_access=True или суперпользователям — то же
    правило, что и в NormativeDocumentAdmin.get_queryset(), продублировано
    здесь осознанно: поиск — самостоятельная точка входа к тем же данным,
    не должна полагаться на проверку допуска где-то выше по стеку вызовов.

    Черновики (status=DRAFT) исключены из результатов — САМОСТОЯТЕЛЬНОЕ,
    НЕ подтверждённое Заказчиком допущение («база знаний» ищет то, что
    когда-либо было официально зарегистрировано, не рабочие черновики),
    см. STACK.md."""
    normalized_query = normalize_term(raw_query)
    if not normalized_query:
        return NormativeDocument.objects.none()

    queryset = NormativeDocument.objects.exclude(status=NormativeDocument.Status.DRAFT)
    if not (getattr(user, "is_authenticated", False) and (user.is_superuser or user.dsp_access)):
        queryset = queryset.filter(access_level=NormativeDocument.AccessLevel.GENERAL)

    vector = (
        SearchVector("title", weight="A", config=SEARCH_CONFIG)
        + SearchVector("reg_number", weight="A", config=SEARCH_CONFIG)
        + SearchVector("summary", weight="B", config=SEARCH_CONFIG)
    )
    # websearch_to_tsquery — прямой пользовательский ввод, произвольный
    # текст (учитывает кавычки/операторы так, как ожидает обычный
    # пользователь поисковика).
    direct_query = SearchQuery(raw_query, config=SEARCH_CONFIG, search_type="websearch")
    queryset = queryset.annotate(direct_rank=SearchRank(vector, direct_query))
    score = F("direct_rank") * DIRECT_MATCH_WEIGHT

    expanded_terms = expand_query(raw_query, category=category, service=service)
    for i, term in enumerate(expanded_terms):
        field_name = f"expansion_rank_{i}"
        # phrase — термины расширения это устойчивые словосочетания из
        # тезауруса (canonical), не свободный пользовательский текст:
        # нужно совпадение как фразы («тяговая подстанция» целиком), а не
        # AND отдельных слов где угодно в документе.
        term_query = SearchQuery(term.text, config=SEARCH_CONFIG, search_type="phrase")
        queryset = queryset.annotate(**{field_name: SearchRank(vector, term_query)})
        score = score + F(field_name) * (term.weight * EXPANSION_WEIGHT)

    queryset = queryset.annotate(
        score=ExpressionWrapper(score, output_field=FloatField()),
    ).filter(score__gt=0).order_by("-score", "-reg_date")
    return queryset
