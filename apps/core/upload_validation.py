"""Fail-closed content validation for newly uploaded files."""
from __future__ import annotations

import os

from django.conf import settings
from django.core.exceptions import ValidationError


class UploadValidationError(ValidationError):
    """Base class for intake validation failures."""


class UploadTooLarge(UploadValidationError):
    """Upload exceeds the common intake byte limit."""


def _source_file(field_file):
    return getattr(field_file, "file", field_file)


def validate_upload_size(field_file) -> None:
    """Reject uploads larger than the configured intake limit.

    Django uploaded files normally expose ``size``.  For generic file-like
    objects without reliable metadata, fall back to seek/tell.  Failure to
    determine size is fail-closed, and the source stream is rewound so later
    format/antivirus/storage stages always see the complete payload.
    """
    limit = settings.UPLOAD_MAX_BYTES
    size = getattr(field_file, "size", None)
    source = _source_file(field_file)

    try:
        if size is None:
            try:
                source.seek(0, os.SEEK_END)
                size = source.tell()
            except (AttributeError, OSError, ValueError) as exc:
                raise UploadValidationError(
                    "Не удалось определить размер файла; файл не сохранён."
                ) from exc

        if size > limit:
            raise UploadTooLarge(
                f"Размер файла превышает допустимые {limit // (1024 * 1024)} МБ."
            )
    finally:
        try:
            source.seek(0)
        except (AttributeError, OSError, ValueError):
            pass
