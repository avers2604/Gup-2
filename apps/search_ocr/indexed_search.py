from __future__ import annotations

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import Case, ExpressionWrapper, F, FloatField, Q, Value, When
from django.db.models.functions import Coalesce, Greatest

from apps.documents.models import NormativeDocument

from .search import (
    EXACT_MATCH_WEIGHT,
    OCR_BODY_WEIGHT,
    SUMMARY_WEIGHT,
    TITLE_WEIGHT,
    ExpandedTerm,
    expand_query,
)

SEARCH_CONFIG = "russian"


def search_documents_indexed(
    user,
    raw_query: str,
    *,
    category: str | None = None,
    service: str | None = None,
):
    """Smart Search using the persisted DocumentSearchIndex vectors."""
    raw_query = raw_query.strip()
    if not raw_query:
        return NormativeDocument.objects.none()

    queryset = NormativeDocument.objects.exclude(status=NormativeDocument.Status.DRAFT)
    if not (getattr(user, "is_authenticated", False) and (user.is_superuser or user.dsp_access)):
        queryset = queryset.filter(access_level=NormativeDocument.AccessLevel.GENERAL)

    direct_term = ExpandedTerm(text=raw_query, weight=1.0, source_entry_id="", via="direct")
    all_terms = [direct_term] + expand_query(raw_query, category=category, service=service)

    exact_fields = []
    exact_filter = Q()
    term_queries = []
    for i, term in enumerate(all_terms):
        exact_name = f"exact_{i}"
        queryset = queryset.annotate(
            **{
                exact_name: Case(
                    When(reg_number__iexact=term.text, then=Value(term.weight * EXACT_MATCH_WEIGHT)),
                    default=Value(0.0),
                    output_field=FloatField(),
                )
            }
        )
        exact_fields.append(F(exact_name))
        exact_filter |= Q(reg_number__iexact=term.text)
        term_queries.append(
            SearchQuery(
                term.text,
                config=SEARCH_CONFIG,
                search_type="websearch" if term.via == "direct" else "phrase",
            )
        )

    combined_query = term_queries[0]
    for query in term_queries[1:]:
        combined_query = combined_query | query

    # GIN-backed candidate reduction. Exact registration-number hits remain
    # eligible even when the read-model row is temporarily missing/stale.
    queryset = queryset.filter(
        exact_filter | Q(search_read_model__combined_vector=combined_query)
    )

    exact_score = Greatest(*exact_fields) if len(exact_fields) > 1 else exact_fields[0]
    score = exact_score

    for vector_field, field_weight in (
        ("title_vector", TITLE_WEIGHT),
        ("summary_vector", SUMMARY_WEIGHT),
        ("ocr_vector", OCR_BODY_WEIGHT),
    ):
        contributions = []
        for i, (term, term_query) in enumerate(zip(all_terms, term_queries, strict=True)):
            rank_name = f"idx_{vector_field}_{i}"
            queryset = queryset.annotate(
                **{
                    rank_name: Coalesce(
                        SearchRank(F(f"search_read_model__{vector_field}"), term_query),
                        Value(0.0),
                    )
                }
            )
            contributions.append(F(rank_name) * term.weight)

        field_score = contributions[0]
        for contribution in contributions[1:]:
            field_score = field_score + contribution
        score = score + field_score * field_weight

    return (
        queryset.annotate(score=ExpressionWrapper(score, output_field=FloatField()))
        .filter(score__gt=0)
        .order_by("-score", "-reg_date")
    )
