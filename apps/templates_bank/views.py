"""Web GUI рабочего места банка бланков (ТЗ 4.3).

До этой партии единственным входом в банк форм была Django admin, причём
скачать из неё бланк линейному сотруднику было нельзя вовсе — а именно
ради выдачи актуальной формы банк и существует.

Тонкий HTTP-слой: права — permissions.py, запись и учёт скачиваний —
services.py; вьюхи только валидируют ввод и рендерят.
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Max
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View

from . import permissions, services
from .forms import TemplateFamilyForm, TemplateVersionForm
from .models import Template, TemplateFamily

PAGE_SIZE = 20


class TemplateFamilyListView(LoginRequiredMixin, View):
    """Список семейств форм с их действующей версией."""

    template_name = "templates_bank/family_list.html"

    def get(self, request):
        query = (request.GET.get("q") or "").strip()
        queryset = TemplateFamily.objects.annotate(
            version_count=Count("templates"),
            last_release=Max("templates__created_at"),
        ).order_by("name")
        if query:
            queryset = queryset.filter(name__icontains=query)

        paginator = Paginator(queryset, PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get("page"))

        # Действующая версия каждого семейства — одним запросом на
        # страницу, а не запросом на строку в шаблоне.
        active = {
            template.family_id: template
            for template in Template.objects.filter(
                family__in=page_obj.object_list, status=Template.Status.ACTIVE
            ).order_by("family", "-created_at")
        }

        return render(request, self.template_name, {
            "page_obj": page_obj,
            "families": [
                {"family": family, "active": active.get(family.id)}
                for family in page_obj.object_list
            ],
            "query": query,
            "can_manage": permissions.can_manage_templates(request.user),
        })


class TemplateFamilyDetailView(LoginRequiredMixin, View):
    """Карточка семейства форм: вся линия версий (ТЗ 4.3.1)."""

    template_name = "templates_bank/family_detail.html"

    def get(self, request, pk):
        family = get_object_or_404(TemplateFamily, pk=pk)
        return render(request, self.template_name, {
            "family": family,
            "versions": services.family_lineage(family),
            "can_manage": permissions.can_manage_templates(request.user),
        })


class TemplateFamilyCreateView(LoginRequiredMixin, View):
    template_name = "templates_bank/family_form.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not permissions.can_manage_templates(request.user):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, self.template_name, {"form": TemplateFamilyForm()})

    def post(self, request):
        form = TemplateFamilyForm(request.POST)
        if form.is_valid():
            family = form.save()
            messages.success(
                request,
                "Семейство форм создано. Выпустите его первую версию.",
            )
            return HttpResponseRedirect(
                reverse("templates_bank:version_create", args=[family.pk])
            )
        return render(request, self.template_name, {"form": form})


class TemplateVersionCreateView(LoginRequiredMixin, View):
    """Выпуск новой версии бланка. Правки существующей версии нет и быть
    не может: ТЗ 4.3.1 требует инкремента версии даже при минорной
    корректировке."""

    template_name = "templates_bank/version_form.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not permissions.can_manage_templates(request.user):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        family = get_object_or_404(TemplateFamily, pk=pk)
        return render(request, self.template_name, {
            "family": family,
            "form": TemplateVersionForm(user=request.user, family=family),
            "current": self._current_version(family),
        })

    def post(self, request, pk):
        family = get_object_or_404(TemplateFamily, pk=pk)
        form = TemplateVersionForm(
            request.POST, request.FILES, user=request.user, family=family
        )
        if form.is_valid():
            try:
                template = services.publish_version(
                    actor=request.user, family=family, form=form
                )
            except ValidationError as error:
                form.add_error(None, error)
            except PermissionDenied as error:
                form.add_error(None, str(error))
            else:
                messages.success(
                    request, f"Выпущена версия {template.version}."
                )
                return HttpResponseRedirect(
                    reverse("templates_bank:family_detail", args=[family.pk])
                )
        return render(request, self.template_name, {
            "family": family, "form": form, "current": self._current_version(family),
        })

    @staticmethod
    def _current_version(family):
        return (
            Template.objects.filter(family=family, status=Template.Status.ACTIVE)
            .order_by("-created_at")
            .first()
        )


class TemplateDownloadView(LoginRequiredMixin, View):
    """Выдача файла бланка с учётом скачивания (ТЗ 4.3.1).

    Файл отдаётся приложением, а не прямой ссылкой в хранилище: иначе
    `download_count` и запись в WORM-журнал о выдаче архивной формы
    обойти можно было бы простым копированием ссылки.
    """

    def get(self, request, pk, field_name):
        template = get_object_or_404(
            Template.objects.select_related("family"), pk=pk
        )
        try:
            field_file = services.register_download(
                actor=request.user, template=template, field_name=field_name
            )
        except ValidationError:
            raise Http404
        except PermissionDenied as error:
            messages.error(request, str(error))
            return HttpResponseRedirect(reverse("templates_bank:family_list"))

        return FileResponse(field_file.open("rb"), as_attachment=True)
