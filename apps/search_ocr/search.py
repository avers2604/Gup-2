"""Smart Search query expansion and document ranking.

FTS vectors are persisted in ``DocumentSearchIndex``. Search requests only
build tsqueries/ranks; expensive ``to_tsvector`` conversion is moved to the
indexing write path.
"""
import re
from dataclasses import dataclass

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import (
    Case,
    ExpressionWrapper,
    F,
    FloatField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Greatest

from apps.documents.models import NormativeDocument

from .models import ThesaurusEntry, ThesaurusStatus
from .normalization import normalize_term
from .read_models import DocumentSearchIndex

EXACT_MATCH_WEIGHT = 1.0
TITLE_WEIGHT = 0.8
SUMMARY_WEIGHT = 0.5
OCR_BODY_WEIGHT = 0.2
LEGACY_SYNONYM_WEIGHT = 0.3
MAX_EXPANDED_TERMS = 5
SEARCH_CONFIG = "russian"


@dataclass(frozen=True)
class ExpandedTerm:
    text: str
    weight: float
    source_entry_id: str
    via: str


def _contains_term(normalized_query: str, term: str) -> bool:
    normalized_term = normalize_term(term)
    if not normalized_term:
        return False
    pattern = r"(?<!\w)" + re.escape(normalized_term) + r"(?!\w)"
    return re.search(pattern, normalized_query) is not None


def expand_query(
    raw_query: str,
    *,
    category: str | None = None,
    service: str | None = None,
) -> list[ExpandedTerm]:
    normalized_query = normalize_term(raw_query)
    if not normalized_query:
        return []

    matches: dict[str, ExpandedTerm] = {}
    verified_entries = ThesaurusEntry.objects.filter(
        status=ThesaurusStatus.VERIFIED
    ).prefetch_related("ambiguity_candidates")

    for entry in verified_entries:
        via = None
        for short_form in entry.short_forms:
            if _contains_term(normalized_query, short_form):
                via = "short_form"
                break
        if via is None:
            for synonym in entry.synonyms:
                if _contains_term(normalized_query, synonym):
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
        candidates = list(entry.ambiguity_candidates.all())
        if candidates:
            candidate = candidates[0]
            facet_matched = bool(
                (category and entry.category == category)
                or (service and entry.service == service)
            )
            weight = 1.0 if facet_matched else candidate.weight

        canonical = entry.canonical
        existing = matches.get(canonical)
        if existing is None or weight > existing.weight:
            matches[canonical] = ExpandedTerm(
                text=canonical,
                weight=weight,
                source_entry_id=entry.id,
                via=via,
            )

    return sorted(matches.values(), key=lambda term: term.weight, reverse=True)[
        :MAX_EXPANDED_TERMS
    ]


def _query_for_term(term: ExpandedTerm) -> SearchQuery:
    return SearchQuery(
        term.text,
        config=SEARCH_CONFIG,
        search_type="websearch" if term.via == "direct" else "phrase",
    )


def search_documents(
    user,
    raw_query: str,
    *,
    category: str | None = None,
    service: str | None = None,
) -> QuerySet:
    normalized_query = normalize_term(raw_query)
    if not normalized_query:
        return NormativeDocument.objects.none()

    direct_term = ExpandedTerm(
        text=raw_query.strip(), weight=1.0, source_entry_id="", via="direct"
    )
    all_terms = [direct_term] + expand_query(
        raw_query, category=category, service=service
    )

    index_qs = DocumentSearchIndex.objects.exclude(
        status=NormativeDocument.Status.DRAFT
    )
    if not (
        getattr(user, "is_authenticated", False)
        and (user.is_superuser or user.dsp_access)
    ):
        index_qs = index_qs.filter(
            access_level=NormativeDocument.AccessLevel.GENERAL
        )

    # Use the combined GIN-backed vector only to cut the candidate set.
    # Exact registration-number matches are admitted independently.
    candidate_filter = Q()
    term_queries: list[tuple[ExpandedTerm, SearchQuery]] = []
    for term in all_terms:
        term_query = _query_for_term(term)
        term_queries.append((term, term_query))
        candidate_filter |= Q(reg_number__iexact=term.text)
        candidate_filter |= Q(search_vector=term_query)
    index_qs = index_qs.filter(candidate_filter)

    exact_parts = []
    for index, (term, _) in enumerate(term_queries):
        alias = f"exact_{index}"
        index_qs = index_qs.annotate(
            **{
                alias: Case(
                    When(
                        reg_number__iexact=term.text,
                        then=Value(term.weight * EXACT_MATCH_WEIGHT),
                    ),
                    default=Value(0.0),
                    output_field=FloatField(),
                )
            }
        )
        exact_parts.append(F(alias))

    score = Greatest(*exact_parts) if len(exact_parts) > 1 else exact_parts[0]
    for vector_field, field_weight in (
        ("title_vector", TITLE_WEIGHT),
        ("summary_vector", SUMMARY_WEIGHT),
        ("ocr_vector", OCR_BODY_WEIGHT),
    ):
        field_score = None
        for index, (term, term_query) in enumerate(term_queries):
            alias = f"{vector_field}_rank_{index}"
            index_qs = index_qs.annotate(
                **{alias: SearchRank(F(vector_field), term_query)}
            )
            weighted = F(alias) * term.weight
            field_score = weighted if field_score is None else field_score + weighted
        score = score + field_score * field_weight

    index_qs = index_qs.annotate(
        score=ExpressionWrapper(score, output_field=FloatField())
    ).filter(score__gt=0)

    score_subquery = index_qs.filter(document_id=OuterRef("pk")).values("score")[:1]
    return (
        NormativeDocument.objects.filter(
            pk__in=Subquery(index_qs.values("document_id"))
        )
        .annotate(
            score=Subquery(score_subquery, output_field=FloatField())
        )
        .order_by("-score", "-reg_date")
    )
