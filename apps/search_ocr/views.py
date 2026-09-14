"""
Web GUI — Smart Search (ТЗ 4.4.1). Серверный рендеринг, GET-based поиск
(закладываемый/шареабельный URL — стандартная семантика поиска). Тонкий
HTTP-слой поверх apps.search_ocr.search — не обращается к
NormativeDocument.objects/ThesaurusEntry.objects напрямую.
"""
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.views import View

from apps.core.business_metrics import build_search_metric_key, record_search
from apps.core.limits import consume_fixed_window

from .forms import SearchForm
from .indexed_search import search_documents_indexed

PAGE_SIZE = 20


class SearchView(LoginRequiredMixin, View):
    """Внутренний Web GUI (для сотрудников) — поиск требует входа, как и
    остальные экраны системы; не публичная страница."""

    template_name = "search_ocr/search.html"

    def get(self, request):
        form = SearchForm(request.GET or None)
        results = None
        page_obj = None
        paginator = None
        query_string = ""

        if form.is_valid() and form.cleaned_data.get("q"):
            window_seconds = int(
                getattr(settings, "WEB_SEARCH_RATE_WINDOW_SECONDS", 60)
            )
            allowed = consume_fixed_window(
                "web-search",
                str(request.user.pk),
                limit=int(getattr(settings, "WEB_SEARCH_RATE_LIMIT", 60)),
                window_seconds=window_seconds,
            )
            if not allowed:
                response = HttpResponse(
                    "Слишком много поисковых запросов.\n",
                    status=429,
                    content_type="text/plain; charset=utf-8",
                )
                response["Retry-After"] = str(window_seconds)
                return response

            query = form.cleaned_data["q"]
            category = form.cleaned_data.get("category") or None
            service = form.cleaned_data.get("service") or None
            queryset = search_documents_indexed(
                request.user,
                query,
                category=category,
                service=service,
            )
            paginator = Paginator(queryset, PAGE_SIZE)
            page_obj = paginator.get_page(request.GET.get("page"))
            logical_key = build_search_metric_key(
                user_id=request.user.pk,
                query=query,
                category=category,
                service=service,
                surface="web",
            )
            record_search(
                paginator.count,
                page_number=page_obj.number,
                logical_search_key=logical_key,
            )
            results = page_obj.object_list
            query_params = request.GET.copy()
            query_params.pop("page", None)
            query_string = query_params.urlencode()

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "results": results,
                "page_obj": page_obj,
                "paginator": paginator,
                "query_string": query_string,
            },
        )
