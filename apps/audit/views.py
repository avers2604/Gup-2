"""Web GUI журнала аудита (ТЗ 4.7).

Журнал вёлся с Этапа 1, но прочитать его можно было только в Django
admin — то есть Офицеру ИБ требовался `is_staff`, а сама роль на доступ
к журналу не влияла никак.

Только чтение и только фильтры: журнал WORM, изменить или удалить запись
нельзя ни из какого контура, и интерфейс не должен даже намекать на
обратное.
"""
import csv
import datetime
import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.core.csv_safety import csv_safe

from . import permissions
from .forms import AuditFilterForm
from .models import AuditLog

PAGE_SIZE = 50


def filtered_entries(form):
    """Отбор записей журнала по фильтрам формы.

    Общая для экрана и для выгрузки: выгрузка обязана отдавать ровно то, что
    человек видит на экране, иначе «выгрузил и проверил» перестаёт совпадать с
    «посмотрел глазами».
    """
    queryset = AuditLog.objects.all().order_by("-created_at")
    if not form.is_valid():
        return queryset

    data = form.cleaned_data
    if data.get("event_type"):
        queryset = queryset.filter(event_type=data["event_type"])
    if data.get("actor_personnel_number"):
        queryset = queryset.filter(
            actor_personnel_number=data["actor_personnel_number"].strip()
        )
    if data.get("object_id"):
        # Совпадение и по идентификатору, и по рег. номеру в реквизитах:
        # object_id по НРД — UUID, а ищет человек по номеру документа,
        # который у него на руках.
        value = data["object_id"].strip()
        queryset = queryset.filter(Q(object_id=value) | Q(details__reg_number=value))
    if data.get("date_from"):
        queryset = queryset.filter(created_at__gte=_start_of_day(data["date_from"]))
    if data.get("date_to"):
        # Включительно по указанную дату: пользователь, выбравший «по 10.09»,
        # ожидает увидеть события этого дня, а не пустой хвост до полуночи.
        queryset = queryset.filter(
            created_at__lt=_start_of_day(data["date_to"] + datetime.timedelta(days=1))
        )
    return queryset


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
        queryset = filtered_entries(form)

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


class AuditLogExportView(LoginRequiredMixin, View):
    """Выгрузка журнала аудита (ТЗ 4.7), фиксирующая сама себя.

    Событие `AUDIT_LOG_EXPORTED` существовало в модели с Этапа 1, но его никто
    не писал: выгрузки не было вовсе, журнал читали только глазами через экран.
    Для ИБ и внешней проверки этого мало — нужна выгрузка за период, которую
    можно приложить к акту.

    Выгрузка журнала безопасности сама является событием безопасности, поэтому
    запись `AUDIT_LOG_EXPORTED` делается ДО отдачи файла и содержит применённые
    фильтры: кто, когда и какой срез журнала вынес наружу. Запись до отдачи, а
    не после, намеренно — иначе оборванная на середине выгрузка не оставила бы
    следа вообще, а это ровно тот случай, который интересен проверяющему.

    Сам CSV — снимок на момент начала запроса. Это важно для потоковой отдачи:
    QuerySet ленивый и фактически читается уже после создания события
    `AUDIT_LOG_EXPORTED`; без верхней границы по `created_at` текущая выгрузка
    могла бы попасть в собственный файл, а `matched_entries` при этом оставался
    бы посчитанным до её записи.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not permissions.can_view_audit_log(request.user):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        # Для выгрузки форма всегда bound: пустой QueryDict означает валидный
        # запрос «выгрузить всё», а невалидные значения должны fail-closed,
        # а не превращаться в полный экспорт журнала.
        form = AuditFilterForm(request.GET)
        if not form.is_valid():
            return HttpResponseBadRequest("Некорректные фильтры выгрузки.\n")

        snapshot_at = timezone.now()
        queryset = filtered_entries(form).filter(created_at__lte=snapshot_at)
        matched_entries = queryset.count()

        applied = {}
        for key, value in form.cleaned_data.items():
            if value in (None, "", []):
                continue
            applied[key] = value.isoformat() if hasattr(value, "isoformat") else str(value)

        AuditLog.objects.create(
            event_type=AuditLog.EventType.AUDIT_LOG_EXPORTED,
            actor=request.user,
            actor_personnel_number=getattr(request.user, "personnel_number", ""),
            object_type="AuditLog",
            object_id="export",
            details={
                "filters": applied,
                "matched_entries": matched_entries,
                "format": "csv",
                "snapshot_at": snapshot_at.isoformat(),
            },
        )

        stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        response = StreamingHttpResponse(
            _export_rows(queryset),
            content_type="text/csv; charset=utf-8",
        )
        response["Content-Disposition"] = f'attachment; filename="audit-log-{stamp}.csv"'
        return response


def _export_rows(queryset):
    """Готовые строки CSV по одной.

    Поток, а не готовый файл в памяти: журнал WORM и только растёт, у него нет
    верхней границы размера. `.iterator()` не даёт ORM материализовать всю
    выборку разом.
    """
    writer = csv.writer(_EchoBuffer(), delimiter=";")
    # BOM — чтобы Excel в русской локали открыл файл без ручного выбора
    # кодировки, как и отчёты импорта персонала.
    yield "﻿" + writer.writerow([
        "Дата и время (UTC)", "Событие", "Табельный номер", "ФИО",
        "Тип объекта", "Идентификатор объекта", "Реквизиты",
    ])
    for entry in queryset.select_related("actor").iterator(chunk_size=500):
        yield writer.writerow([
            entry.created_at.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            csv_safe(entry.get_event_type_display()),
            csv_safe(entry.actor_personnel_number),
            csv_safe(entry.actor.full_name if entry.actor else ""),
            csv_safe(entry.object_type),
            csv_safe(entry.object_id),
            csv_safe(json.dumps(entry.details, ensure_ascii=False, sort_keys=True)),
        ])


class _EchoBuffer:
    """Буфер, который ничего не хранит: csv.writer пишет в него, а `write()`
    возвращает готовую строку прямо в поток ответа."""

    def write(self, value):
        return value
