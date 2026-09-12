"""Fail-closed content validation for newly uploaded files."""
from __future__ import annotations

import os
import zipfile
from xml.etree import ElementTree

from django.conf import settings
from django.core.exceptions import ValidationError


class UploadValidationError(ValidationError):
    """Base class for intake validation failures."""


class UploadTooLarge(UploadValidationError):
    """Upload exceeds the common intake byte limit."""


class InvalidOOXML(UploadValidationError):
    """DOCX/XLSX upload is not a valid package of the declared family."""


def _source_file(field_file):
    return getattr(field_file, "file", field_file)


def validate_upload_size(field_file) -> None:
    """Reject uploads larger than the configured intake limit.

    Django uploaded files normally expose ``size``. For generic file-like
    objects without reliable metadata, fall back to seek/tell. Failure to
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


CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
OOXML_CONTRACTS = {
    "docx": (
        "word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    ),
    "xlsx": (
        "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    ),
}


def validate_ooxml(field_file, expected_kind: str) -> None:
    """Prove that an upload is a readable DOCX/XLSX package of the requested kind.

    This is intentionally structural rather than full ECMA-376 schema
    validation. Macro/ActiveX policy remains in ``apps.core.macro_check``.
    """
    try:
        main_part, expected_type = OOXML_CONTRACTS[expected_kind]
    except KeyError as exc:
        raise InvalidOOXML("Файл не соответствует допустимому формату DOCX/XLSX.") from exc

    source = _source_file(field_file)
    try:
        source.seek(0)
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            for required in ("[Content_Types].xml", "_rels/.rels", main_part):
                if names.count(required) != 1:
                    raise InvalidOOXML("Некорректная структура файла DOCX/XLSX.")

            try:
                content_types = ElementTree.fromstring(
                    archive.read("[Content_Types].xml")
                )
                ElementTree.fromstring(archive.read("_rels/.rels"))
                archive.read(main_part)
            except (
                ElementTree.ParseError,
                KeyError,
                RuntimeError,
                zipfile.BadZipFile,
            ) as exc:
                raise InvalidOOXML("Некорректная структура файла DOCX/XLSX.") from exc

            declared_types = {
                node.attrib.get("PartName"): node.attrib.get("ContentType")
                for node in content_types.findall(f"{{{CONTENT_TYPES_NS}}}Override")
            }
            if declared_types.get(f"/{main_part}") != expected_type:
                raise InvalidOOXML(
                    "Файл не соответствует заявленному формату DOCX/XLSX."
                )
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise InvalidOOXML("Некорректная структура файла DOCX/XLSX.") from exc
    finally:
        try:
            source.seek(0)
        except (AttributeError, OSError, ValueError):
            pass
