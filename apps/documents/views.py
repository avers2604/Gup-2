"""Web GUI рабочего места НРД (ТЗ 4.1, 4.2): реестр, карточка, запись.

До появления этих страниц единственным интерфейсом к карточкам НРД была
Django admin («временный интерфейс… до появления рабочих мест из ТЗ 4.1»
— README). Здесь собственный контур: серверный рендеринг, сессия + CSRF,
та же дизайн-система.

Тонкий HTTP-слой: правила доступа — apps/documents/permissions.py,
допустимые переходы статуса — transitions.py, запись — services.py;
вьюхи только валидируют ввод формой, вызывают сервис и рендерят ответ.
Ни одного обращения к Model.objects.* на запись — та же DDD-граница, что
объявлена в STACK.md для apps/iam.
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View

from . import permissions, services, transitions
from .forms import DocumentFilterForm, DocumentForm, RelationForm, StatusChangeForm
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
            "can_manage_relations": permissions.can_manage_relations(request.user, document),
            "activity": _document_activity(document),
        })


class _DocumentWriteMixin(LoginRequiredMixin):
    """Общее для страниц записи: карточка берётся только из видимых
    пользователю — документ «ДСП» без допуска даёт 404, как и в карточке
    чтения, чтобы страница правки не подтверждала существование того,
    что скрыто от читателя.

    Нехватка полномочий на видимом документе тоже отвечает 404, а не
    403: страница, которой у пользователя нет, и не должна существовать
    в его картине интерфейса — ссылок на неё ему нигде не показывают.
    Отказ, о котором сообщать полезно (сервис отклонил уже отправленную
    форму), приходит иначе — сообщением в самой форме, а не кодом
    ответа.
    """

    def get_document(self, request, pk):
        return get_object_or_404(
            permissions.visible_documents(request.user).select_related("issuer_dept"), pk=pk
        )


class DocumentCreateView(_DocumentWriteMixin, View):
    """Регистрация новой карточки НРД. Всегда создаёт черновик."""

    template_name = "documents/document_form.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not permissions.can_edit_document(request.user):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, self.template_name, {
            "form": DocumentForm(), "document": None,
        })

    def post(self, request):
        form = DocumentForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                document = services.create_document(actor=request.user, form=form)
            except ValidationError as error:
                # Модельная валидация (full_clean в сервисе) — например,
                # категория срока хранения, неприменимая к карточке НРД.
                # Показываем её в форме, а не 500-й страницей.
                form.add_error(None, error)
            except PermissionDenied as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, f"Карточка {document.reg_number} создана как черновик.")
                return HttpResponseRedirect(
                    reverse("documents:detail", args=[document.pk])
                )
        return render(request, self.template_name, {"form": form, "document": None})


class DocumentUpdateView(_DocumentWriteMixin, View):
    """Правка карточки. Только черновик — см. permissions.can_edit_document."""

    template_name = "documents/document_form.html"

    def get(self, request, pk):
        document = self.get_document(request, pk)
        if not permissions.can_edit_document(request.user, document):
            raise Http404
        return render(request, self.template_name, {
            "form": DocumentForm(instance=document), "document": document,
        })

    def post(self, request, pk):
        document = self.get_document(request, pk)
        if not permissions.can_edit_document(request.user, document):
            raise Http404
        form = DocumentForm(request.POST, request.FILES, instance=document)
        if form.is_valid():
            try:
                services.update_document(actor=request.user, document=document, form=form)
            except ValidationError as error:
                form.add_error(None, error)
            except PermissionDenied as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, "Изменения сохранены.")
                return HttpResponseRedirect(reverse("documents:detail", args=[document.pk]))
        return render(request, self.template_name, {"form": form, "document": document})


class DocumentStatusChangeView(_DocumentWriteMixin, View):
    """Смена статуса: публикация, внесение изменений, отмена, архивирование."""

    template_name = "documents/document_status_form.html"

    def get(self, request, pk):
        document = self.get_document(request, pk)
        if not permissions.can_change_status(request.user, document):
            raise Http404
        return render(request, self.template_name, {
            "document": document,
            "form": StatusChangeForm(document=document, user=request.user),
            "allowed": _allowed_for(request.user, document),
        })

    def post(self, request, pk):
        document = self.get_document(request, pk)
        if not permissions.can_change_status(request.user, document):
            raise Http404
        form = StatusChangeForm(request.POST, document=document, user=request.user)
        if form.is_valid():
            try:
                _, previous = services.change_document_status(
                    actor=request.user,
                    document=document,
                    new_status=form.cleaned_data["new_status"],
                    comment=form.cleaned_data["comment"],
                )
            except ValidationError as error:
                # Сюда же попадает StatusTransitionError — переход,
                # прошедший форму, но отклонённый сервисом (статус мог
                # измениться между отрисовкой страницы и отправкой).
                form.add_error(None, error)
            except PermissionDenied as error:
                form.add_error(None, str(error))
            else:
                document.refresh_from_db()
                messages.success(
                    request,
                    f"Статус изменён: «{dict(type(document).Status.choices)[previous]}» → "
                    f"«{document.get_status_display()}».",
                )
                return HttpResponseRedirect(reverse("documents:detail", args=[document.pk]))
        return render(request, self.template_name, {
            "document": document, "form": form,
            "allowed": _allowed_for(request.user, document),
        })


class DocumentRelationCreateView(_DocumentWriteMixin, View):
    """Завести связь версионности из карточки документа (ТЗ 4.2.2).

    Отдельная страница, а не форма внутри карточки: у связи свой набор
    ошибок (цикл, дубль, недоступная цель), и показывать их посреди
    карточки на 5 разделов — значит прятать их от пользователя.
    """

    template_name = "documents/relation_form.html"

    def get(self, request, pk):
        document = self._document_for_relations(request, pk)
        return render(request, self.template_name, {
            "document": document,
            "form": RelationForm(user=request.user, from_document=document),
        })

    def post(self, request, pk):
        document = self._document_for_relations(request, pk)
        form = RelationForm(request.POST, user=request.user, from_document=document)
        if form.is_valid():
            try:
                services.add_relation(
                    actor=request.user, from_document=document,
                    to_document=form.cleaned_data["to_document"],
                    relation_type=form.cleaned_data["relation_type"],
                    note=form.cleaned_data.get("note", ""),
                )
            except (ValidationError, IntegrityError) as error:
                # Обычный дубль ловит ещё валидация формы (ModelForm
                # проверяет UniqueConstraint до сохранения). IntegrityError
                # сюда доходит только в гонке — две одинаковые связи,
                # отправленные одновременно, — и без этой ветки такая
                # гонка выглядела бы как 500-я страница.
                form.add_error(None, _relation_error_message(error))
            except PermissionDenied as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, "Связь версионности добавлена.")
                return HttpResponseRedirect(reverse("documents:detail", args=[document.pk]))
        return render(request, self.template_name, {"document": document, "form": form})

    def _document_for_relations(self, request, pk):
        document = self.get_document(request, pk)
        if not permissions.can_manage_relations(request.user, document):
            raise Http404
        return document


class DocumentRelationDeleteView(_DocumentWriteMixin, View):
    """Снять связь. Только POST: удаление по GET-ссылке сработало бы от
    любого предзагрузчика ссылок в браузере или почтовом клиенте."""

    def post(self, request, pk, relation_id):
        document = self.get_document(request, pk)
        if not permissions.can_manage_relations(request.user, document):
            raise Http404
        relation = get_object_or_404(
            DocumentRelation, pk=relation_id, from_document=document
        )
        try:
            services.remove_relation(actor=request.user, relation=relation)
        except PermissionDenied as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Связь версионности снята.")
        return HttpResponseRedirect(reverse("documents:detail", args=[document.pk]))


def _relation_error_message(error):
    """Читаемое сообщение вместо текста ограничения БД.

    ValidationError уже написан по-русски: цикл объясняет
    DocumentRelation.clean(), дубль — violation_error_message самого
    UniqueConstraint. А вот IntegrityError приходит текстом Postgres про
    unique_document_relation, который пользователю ни о чём не говорит.
    """
    if isinstance(error, IntegrityError):
        return "Такая связь между этими документами уже заведена."
    return error


def _allowed_for(user, document):
    """Переходы, доступные именно этому пользователю на этом документе.

    Шаблон по этому списку решает, показывать ли форму или сообщение
    «переходов не предусмотрено», — и для Контролёра/Юриста на
    действующем документе список не должен включать откат и
    аннулирование, оставленные Администратору.
    """
    return [
        (value, label)
        for value, label in transitions.target_choices(document.status)
        if permissions.can_change_status(user, document, value)
    ]


ACTIVITY_LIMIT = 20


def _document_activity(document):
    """Записи WORM-журнала по этому документу — «кто опубликовал, кто
    отменил, кто менял связи» (решение Заказчика по доступу к журналу).

    Видна всем, у кого есть доступ к карточке: это не контроль
    безопасности, а обычная работа — до этого Методист не мог узнать, кто
    опубликовал его же приказ, иначе как через Офицера ИБ. Полный журнал
    по-прежнему закрыт (`apps/audit/permissions.py`).

    Записи ищутся и по UUID, и по регистрационному номеру: с этой партии
    журнал по НРД ключуется UUID (как бланки и пользователи), но записи,
    сделанные раньше, привязаны к рег. номеру, а журнал WORM — переписать
    их нельзя. Совпадение по номеру может принадлежать другой карточке с
    тем же номером (он не уникален) — цена обратной совместимости, и она
    уменьшается сама по мере накопления новых записей.
    """
    from django.db.models import Q

    from apps.audit.models import AuditLog

    return (
        AuditLog.objects.filter(
            Q(object_id=str(document.pk)) | Q(object_id=document.reg_number),
            object_type="NormativeDocument",
        )
        .select_related("actor")
        .order_by("-created_at")[:ACTIVITY_LIMIT]
    )
