"""
Web GUI — Smart Search (ТЗ 4.4.1). Серверный рендеринг, GET-based поиск
(закладываемый/шареабельный URL — стандартная семантика поиска). Тонкий
HTTP-слой поверх apps.search_ocr.search — не обращается к
NormativeDocument.objects/ThesaurusEntry.objects напрямую.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views import View

from .forms import SearchForm
from .search import search_documents

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
            queryset = search_documents(
                request.user,
                form.cleaned_data["q"],
                category=form.cleaned_data.get("category") or None,
                service=form.cleaned_data.get("service") or None,
            )
            paginator = Paginator(queryset, PAGE_SIZE)
            page_obj = paginator.get_page(request.GET.get("page"))
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
