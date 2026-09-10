from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.business_metrics import record_search

from .forms import SearchForm
from .indexed_search import search_documents_indexed
from .throttles import SearchApiThrottle


class SearchPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 50


class DocumentSearchAPIView(APIView):
    """Пагинированный API поиска документов для интеграций."""

    throttle_classes = [SearchApiThrottle]
    pagination_class = SearchPagination

    @extend_schema(
        responses={200: OpenApiResponse(description="Постраничный список найденных документов")},
    )
    def get(self, request):
        form = SearchForm(request.query_params)
        if not form.is_valid():
            return Response({"errors": form.errors}, status=status.HTTP_400_BAD_REQUEST)

        query = form.cleaned_data.get("q")
        if not query:
            return Response({"count": 0, "next": None, "previous": None, "results": []})

        queryset = search_documents_indexed(
            request.user,
            query,
            category=form.cleaned_data.get("category") or None,
            service=form.cleaned_data.get("service") or None,
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        if page is None:
            page = []
            count = len(page)
        else:
            count = paginator.page.paginator.count
        record_search(count)

        results = [
            {
                "reg_number": doc.reg_number,
                "title": doc.title,
                "status": doc.status,
                "status_display": doc.get_status_display(),
                "reg_date": doc.reg_date,
                "score": getattr(doc, "score", None),
            }
            for doc in page
        ]
        return paginator.get_paginated_response(results)
