"""Web GUI журнала аудита (ТЗ 4.7).

Журнал вёлся с Этапа 1, но прочитать его можно было только в Django
admin — то есть Офицеру ИБ требовался `is_staff`, а сама роль на доступ
к журналу не влияла никак.

Только чтение и только фильтры: журнал WORM, изменить или удалить запись
нельзя ни из какого контура, и интерфейс не должен даже намекать на
обратное.
"""
import datetime

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from . import permissions
from .forms import AuditFilterForm
from .models import AuditLog

PAGE_SIZE = 50


class AuditLogListView(LoginRequiredMixin, View):
    template_name = "audit/audit_list.html"

    def dispatch(self, request, *args, **kwargs):
        # 404, а не 403: страницы, которой у пользователя нет, не должно
        # быть и в его картине интерфейса — ссылку на журнал ему тоже
        # нигде не показывают.
        if request.user.is_authenticated and not permissions.can_view_audit_log(request.user):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        form = AuditFilterForm(request.GET or None)
        queryset = AuditLog.objects.all().order_by("-created_at")

        if form.is_valid():
            data = form.cleaned_data
            if data.get("event_type"):
                queryset = queryset.filter(event_type=data["event_type"])
            if data.get("actor_personnel_number"):
                queryset = queryset.filter(
                    actor_personnel_number=data["actor_personnel_number"].strip()
                )
            if data.get("object_id"):
                # Совпадение и по идентификатору, и по рег. номеру в
                # реквизитах: object_id по НРД — UUID, а ищет человек по
                # номеру документа, который у него на руках.
                value = data["object_id"].strip()
                queryset = queryset.filter(
                    Q(object_id=value) | Q(details__reg_number=value)
                )
            if data.get("date_from"):
                queryset = queryset.filter(created_at__gte=_start_of_day(data["date_from"]))
            if data.get("date_to"):
                # Включительно по указанную дату: пользователь, выбравший
                # «по 10.09», ожидает увидеть события этого дня, а не
                # пустой хвост до полуночи.
                queryset = queryset.filter(created_at__lt=_start_of_day(
                    data["date_to"] + datetime.timedelta(days=1)
                ))

        paginator = Paginator(queryset, PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get("page"))

        query_params = request.GET.copy()
        query_params.pop("page", None)

        return render(request, self.template_name, {
            "form": form,
            "page_obj": page_obj,
            "entries": page_obj.object_list,
            "query_string": query_params.urlencode(),
        })


def _start_of_day(date):
    return timezone.make_aware(
        datetime.datetime.combine(date, datetime.time.min),
        timezone.get_current_timezone(),
    )
