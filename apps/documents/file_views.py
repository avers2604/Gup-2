from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET

from apps.core.business_metrics import record_link_generation_failure

from . import permissions
from .models import NormativeDocument


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
    """Generate/redirect to a storage URL while measuring 403/404/504 failures."""
    field_name = _ALLOWED_FIELDS.get(kind)
    if field_name is None:
        record_link_generation_failure(404)
        raise Http404

    try:
        document = permissions.visible_documents(request.user).get(pk=pk)
    except NormativeDocument.DoesNotExist as exc:
        record_link_generation_failure(404)
        raise Http404 from exc

    field_file = getattr(document, field_name)
    if not field_file:
        record_link_generation_failure(404)
        raise Http404

    try:
        url = field_file.url
    except Exception as exc:
        status_code = _status_from_exception(exc)
        record_link_generation_failure(status_code)
        return HttpResponse("link unavailable\n", status=status_code, content_type="text/plain")

    return redirect(url)
