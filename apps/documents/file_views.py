from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET

from apps.core.business_metrics import record_link_generation_failure
from apps.core.limits import client_ip
from apps.core.staged_files import promotion_pending_for

from . import permissions
from .models import NormativeDocument


logger = logging.getLogger(__name__)

_ALLOWED_FIELDS = {
    "original": "files_original",
    "editable": "files_editable",
}


def _status_from_exception(exc: Exception) -> int:
    if isinstance(exc, PermissionError):
        return 403
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in (403, 404, 504):
            return int(code)
    return 504


@login_required
@require_GET
def document_file_link(request, pk, kind: str):
    """Generate/redirect to a storage URL while measuring storage failures.

    ``files_original`` is not downloadable while a durable WORM promotion is
    pending. The database deliberately contains the final immutable key before
    the copy happens, so resolving ``FieldFile.url`` during that window would
    expose a link to an object that does not exist yet. We return 409 instead;
    this is an expected transient application state and is not counted as a
    MinIO/link-generation failure.

    Выдача файла документа с грифом ДСП пишется в WORM-журнал событием
    ``EXPORT_RESTRICTED`` (ТЗ 4.7) — по тому же принципу, что и
    ``ARCHIVE_DOWNLOAD`` для архивных бланков.

    Выдача сериализована с изменением карточки row lock-ом. Это закрывает
    окно GENERAL -> RESTRICTED между проверкой доступа и возвратом presigned
    URL. После генерации ссылки карточка всё равно перечитывается через
    `visible_documents()`: это fail-closed защита от изменений внутри той же
    транзакции/хуков и одновременно источник актуального грифа для WORM-аудита.
    """
    field_name = _ALLOWED_FIELDS.get(kind)
    if field_name is None:
        raise Http404

    with transaction.atomic():
        try:
            document = (
                permissions.visible_documents(request.user)
                .select_for_update()
                .get(pk=pk)
            )
        except NormativeDocument.DoesNotExist as exc:
            raise Http404 from exc

        field_file = getattr(document, field_name)
        if not field_file:
            raise Http404

        issued_file_name = field_file.name

        if field_name == "files_original" and promotion_pending_for(
            "documents.normativedocument",
            str(document.pk),
            field_name,
            issued_file_name,
        ):
            response = HttpResponse(
                "file is being secured in immutable storage; retry later\n",
                status=409,
                content_type="text/plain",
            )
            response["Retry-After"] = "5"
            return response

        try:
            url = field_file.url
        except Exception as exc:
            status_code = _status_from_exception(exc)
            # Only real storage/link-generation failures are measured here. Bad
            # route/document/field input above is a business 404 and must not pollute
            # the Stage 4 infrastructure indicator.
            logger.exception(
                "Не удалось получить ссылку на файл документа %s (%s)", pk, field_name,
            )
            record_link_generation_failure(status_code)
            return HttpResponse("link unavailable\n", status=status_code, content_type="text/plain")

        # Не отдаём уже сгенерированную ссылку, пока повторно не доказали, что
        # текущая карточка всё ещё видима этому пользователю и указывает ровно
        # на тот файл, для которого URL был выпущен. В нормальной конкуренции
        # select_for_update выше не даст другой транзакции изменить строку; этот
        # re-read также закрывает изменения из кода, выполняющегося в той же
        # транзакции (и делает защитную границу явной для будущих изменений).
        try:
            current = permissions.visible_documents(request.user).get(pk=pk)
        except NormativeDocument.DoesNotExist as exc:
            raise Http404 from exc

        current_field = getattr(current, field_name)
        if not current_field or current_field.name != issued_file_name:
            raise Http404

        _record_restricted_export(request, current, kind)
        return redirect(url)


def _record_restricted_export(request, document, kind: str) -> None:
    """Событие выдачи файла документа с грифом ДСП.

    Пишется ТОЛЬКО для `RESTRICTED`: журналировать каждое скачивание каждого
    общедоступного документа — значит утопить в шуме именно те записи, ради
    которых журнал и заводился.

    Порядок важен: ссылка уже получена, но пользователю ещё не отдана. Если
    запись в журнал упадёт, вызов завершится ошибкой и ссылка не уйдёт —
    выдача ДСП без следа в WORM-журнале недопустима. Неудачная генерация
    ссылки, наоборот, доступа не даёт, и события не порождает.

    Сама presigned-ссылка в журнал НЕ попадает: до истечения срока она
    работает как предъявительский пропуск к файлу, а журнал аудита читают
    шире, чем сам ДСП-документ.
    """
    if document.access_level != NormativeDocument.AccessLevel.RESTRICTED:
        return

    from apps.audit.models import AuditLog

    actor = request.user
    AuditLog.objects.create(
        event_type=AuditLog.EventType.EXPORT_RESTRICTED,
        actor=actor,
        actor_personnel_number=getattr(actor, "personnel_number", ""),
        object_type="NormativeDocument",
        object_id=str(document.pk),
        details={
            "reg_number": document.reg_number,
            "kind": kind,
            "status": document.status,
            "ip_address": client_ip(request),
        },
    )
