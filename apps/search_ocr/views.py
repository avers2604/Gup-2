"""
Web GUI — Smart Search (ТЗ 4.4.1). Серверный рендеринг, GET-based поиск
(закладываемый/шареабельный URL — стандартная семантика поиска). Тонкий
HTTP-слой поверх apps.search_ocr.search — не обращается к
NormativeDocument.objects/ThesaurusEntry.objects напрямую.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.views import View

from .forms import SearchForm
from .search import search_documents

# Результаты на странице — поиск ещё без пагинации (Этап 2/3, если
# понадобится: сейчас база документов небольшая, а жёсткий предел
# страхует от неограниченно тяжёлого запроса по очень общему слову).
MAX_RESULTS = 50


class SearchView(LoginRequiredMixin, View):
    """Внутренний Web GUI (для сотрудников) — поиск требует входа, как и
    остальные экраны системы; не публичная страница."""

    template_name = "search_ocr/search.html"

    def get(self, request):
        form = SearchForm(request.GET or None)
        results = None
        if form.is_valid() and form.cleaned_data.get("q"):
            results = search_documents(
                request.user,
                form.cleaned_data["q"],
                category=form.cleaned_data.get("category") or None,
                service=form.cleaned_data.get("service") or None,
            )[:MAX_RESULTS]
        return render(request, self.template_name, {"form": form, "results": results})
