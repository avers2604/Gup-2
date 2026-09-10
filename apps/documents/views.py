"""Web GUI рабочих мест — реестр и карточка НРД (ТЗ 4.1, 4.2).

До этой партии единственным интерфейсом к карточкам НРД была Django
admin («временный интерфейс… до появления рабочих мест из ТЗ 4.1» —
README). Здесь появляется собственный контур: серверный рендеринг,
сессия + CSRF, та же дизайн-система.

Тонкий HTTP-слой: правила доступа — apps/documents/permissions.py,
доменные данные — модели; вьюхи только собирают контекст и рендерят,
никакой бизнес-логики (та же DDD-граница, что объявлена в STACK.md для
apps/iam).
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.views import View

from . import permissions
from .forms import DocumentFilterForm
from .models import DocumentRelation, DocumentStatusHistory

# Тот же размер страницы, что и в Smart Search (apps/search_ocr/views.py)
# — реестр и результаты поиска показывают один и тот же тип строк, разная
# постраничность выглядела бы как случайность.
PAGE_SIZE = 20


class DocumentListView(LoginRequiredMixin, View):
    """Реестр НРД с фильтрами. Документы «ДСП» не попадают в выборку без
    допуска — фильтрация в permissions.visible_documents(), не здесь."""

    template_name = "documents/document_list.html"

    def get(self, request):
        form = DocumentFilterForm(request.GET or None)
        queryset = (
            permissions.visible_documents(request.user)
            .select_related("issuer_dept")
            .order_by("-reg_date", "reg_number")
        )

        if form.is_valid():
            data = form.cleaned_data
            if data.get("doc_type"):
                queryset = queryset.filter(doc_type=data["doc_type"])
            if data.get("status"):
                queryset = queryset.filter(status=data["status"])
            if data.get("issuer_dept"):
                queryset = queryset.filter(issuer_dept=data["issuer_dept"])
            if data.get("effective_from"):
                queryset = queryset.filter(effective_date__gte=data["effective_from"])
            if data.get("effective_to"):
                queryset = queryset.filter(effective_date__lte=data["effective_to"])

        paginator = Paginator(queryset, PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get("page"))

        # Параметры фильтров без page — чтобы навигация по страницам не
        # сбрасывала фильтры (тот же приём, что в Smart Search).
        query_params = request.GET.copy()
        query_params.pop("page", None)

        return render(request, self.template_name, {
            "form": form,
            "page_obj": page_obj,
            "paginator": paginator,
            "results": page_obj.object_list,
            "query_string": query_params.urlencode(),
            "can_edit": permissions.can_edit_document(request.user),
        })


class DocumentDetailView(LoginRequiredMixin, View):
    """Карточка НРД: атрибуты ТЗ 4.2.1, связи версионности (ТЗ 4.2.2) и
    история статусов (ТЗ 4.2.3).

    Документ «ДСП» без допуска отдаёт 404, а не 403: 403 подтвердил бы
    сам факт существования документа с таким номером — тот же принцип
    неразличимости ответов, что и у формы входа (см. STACK.md про
    одинаковое сообщение на неверный номер/пароль).
    """

    template_name = "documents/document_detail.html"

    def get(self, request, pk):
        # Адресация по UUID, а не по reg_number: регистрационный номер в
        # модели НЕ уникален (только индекс, без ограничения), поэтому по
        # нему карточка в общем случае не адресуется однозначно.
        document = get_object_or_404(
            permissions.visible_documents(request.user).select_related("issuer_dept"),
            pk=pk,
        )

        relations_out = (
            DocumentRelation.objects.filter(from_document=document)
            .select_related("to_document")
        )
        relations_in = (
            DocumentRelation.objects.filter(to_document=document)
            .select_related("from_document")
        )
        # Связанные карточки тоже могут быть «ДСП» — показывать их номера
        # пользователю без допуска нельзя, иначе гриф обходится через
        # граф связей соседнего документа.
        visible_pks = set(
            permissions.visible_documents(request.user)
            .filter(
                pk__in=[relation.to_document_id for relation in relations_out]
                + [relation.from_document_id for relation in relations_in]
            )
            .values_list("pk", flat=True)
        )

        return render(request, self.template_name, {
            "document": document,
            "relations_out": [r for r in relations_out if r.to_document_id in visible_pks],
            "relations_in": [r for r in relations_in if r.from_document_id in visible_pks],
            "status_history": DocumentStatusHistory.objects.filter(
                document=document
            ).order_by("-period"),
            "applied_depts": document.applied_depts.all(),
            "tags": document.category_tags.all(),
            "can_edit": permissions.can_edit_document(request.user, document),
            "can_change_status": permissions.can_change_status(request.user, document),
        })
