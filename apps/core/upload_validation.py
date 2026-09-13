"""Fail-closed content validation for newly uploaded files."""
from __future__ import annotations

from contextlib import contextmanager
import logging
import os
import shutil
import tempfile
import zipfile
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from django.conf import settings
from django.core.exceptions import ValidationError
from pdf2image import pdfinfo_from_path
from pdf2image.exceptions import (
    PDFInfoNotInstalledError,
    PDFPageCountError,
    PDFPopplerTimeoutError,
)

logger = logging.getLogger(__name__)


class UploadValidationError(ValidationError):
    """Base class for intake validation failures."""


class UploadTooLarge(UploadValidationError):
    """Upload exceeds the common intake byte limit."""


class InvalidOOXML(UploadValidationError):
    """DOCX/XLSX upload is not a valid package of the declared family."""


class InvalidPDF(UploadValidationError):
    """PDF upload cannot be parsed into a non-empty document."""


class PDFAValidationFailed(UploadValidationError):
    """PDF is structurally readable but does not conform to PDF/A-2b."""


class FormatValidatorUnavailable(UploadValidationError):
    """External format-validation runtime is unavailable or failed closed."""


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


@contextmanager
def _temporary_upload_path(field_file):
    """Materialize an upload in bounded chunks and always remove the temp file."""
    source = _source_file(field_file)
    path = None

    try:
        source.seek(0)
        with tempfile.NamedTemporaryFile(
            prefix="bz-get-upload-", suffix=".bin", delete=False
        ) as temporary:
            path = temporary.name
            shutil.copyfileobj(source, temporary, length=1024 * 1024)
            temporary.flush()
        yield path
    finally:
        try:
            source.seek(0)
        except (AttributeError, OSError, ValueError):
            pass
        if path:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def _validate_pdf_path(path: str) -> None:
    """Validate an on-disk PDF through Poppler without exposing runtime details."""
    try:
        info = pdfinfo_from_path(path, timeout=settings.OCR_PROCESS_TIMEOUT)
    except PDFPageCountError as exc:
        raise InvalidPDF("Некорректный PDF-файл.") from exc
    except (PDFInfoNotInstalledError, PDFPopplerTimeoutError, OSError) as exc:
        logger.exception("PDF structural validator unavailable")
        raise FormatValidatorUnavailable(
            "Проверка формата временно недоступна; файл не сохранён."
        ) from exc

    try:
        pages = int(info.get("Pages", 0))
    except (TypeError, ValueError) as exc:
        raise InvalidPDF("Некорректный PDF-файл.") from exc
    if pages < 1:
        raise InvalidPDF("Некорректный PDF-файл.")


def validate_pdf(field_file) -> None:
    """Validate an uploaded ordinary PDF structurally through Poppler."""
    with _temporary_upload_path(field_file) as path:
        _validate_pdf_path(path)


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
                ParseError,
                DefusedXmlException,
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
