from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET

from apps.core.business_metrics import record_link_generation_failure
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
    """
    field_name = _ALLOWED_FIELDS.get(kind)
    if field_name is None:
        raise Http404

    try:
        document = permissions.visible_documents(request.user).get(pk=pk)
    except NormativeDocument.DoesNotExist as exc:
        raise Http404 from exc

    field_file = getattr(document, field_name)
    if not field_file:
        raise Http404

    if field_name == "files_original" and promotion_pending_for(
        "documents.normativedocument",
        str(document.pk),
        field_name,
        field_file.name,
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

    return redirect(url)
