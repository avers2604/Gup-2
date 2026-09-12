# Pre-WORM PDF/A and OOXML Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce a 150 MiB intake limit and validate real OOXML/PDF/PDF-A content before any upload can reach mutable storage, staging, or WORM promotion.

**Architecture:** Add `apps/core/upload_validation.py` as the only content-validation boundary. Models invoke `size -> format -> ClamAV -> macro policy -> save/stage` only for new uncommitted uploads. PDF and PDF/A validators share one secure temporary-file helper; PDF/A validates ordinary PDF structure with Poppler first, then runs the pinned local veraPDF 1.30.2 CLI against the same temporary file.

**Tech Stack:** Django 5.2, Python 3.13, `zipfile`, `xml.etree.ElementTree`, `tempfile`, `subprocess`, `pdf2image.pdfinfo_from_path`/Poppler, veraPDF Greenfield 1.30.2, GitHub Actions, PostgreSQL 16/18.

**Spec:** `docs/superpowers/specs/2026-09-12-upload-validation-design.md`

## Global Constraints

- Intake limit is exactly `150 * 1024 * 1024` bytes; exactly 150 MiB passes and one byte more fails.
- `NormativeDocument.files_original` accepts only PDF/A-2b.
- `NormativeDocument.files_editable` and `Template.file_editable` accept only structurally valid OOXML matching `.docx` or `.xlsx`.
- `Template.file_sample` requires a structurally valid ordinary PDF and does not require PDF/A.
- PDF/A policy is fixed to veraPDF flavour `2b`; it is not configurable.
- veraPDF production pin is Greenfield 1.30.2.
- Missing, failed, or timed-out Poppler/veraPDF is fail-closed.
- Validation runs before ClamAV, macro detection, `FileField.pre_save()`, staging, or WORM promotion.
- Existing stored file names are not revalidated on unrelated saves; `antivirus.needs_scan(field_file)` remains the new-upload guard.
- No database schema change and no new audit event family are introduced.
- Existing malware and macro audit semantics remain unchanged.

---

## File Map

**Create**

- `apps/core/upload_validation.py` — exceptions, size validator, OOXML validator, shared temp-file helper, Poppler validator, veraPDF validator.
- `apps/core/tests/test_upload_validation.py` — deterministic unit tests plus real-runtime smoke tests.
- `apps/core/tests/upload_fixtures.py` — valid minimal DOCX/XLSX builders and PDF fixture loader.
- `apps/core/tests/fixtures/pdfa-2b-valid.pdf` — upstream veraPDF-corpus PDF/A-2b pass fixture.
- `apps/core/tests/fixtures/pdfa-2b-invalid.pdf` — upstream veraPDF-corpus structurally valid PDF/A-2b fail fixture.
- `apps/core/tests/fixtures/README.md` — fixture provenance and CC BY 4.0 attribution.
- `apps/documents/tests/test_upload_format_validation.py` — document boundary ordering/no-storage regressions.
- `apps/templates_bank/test_upload_format_validation.py` — template boundary ordering/no-storage regressions.
- `deploy/verapdf/auto-install.xml` — headless IzPack descriptor.
- `deploy/verapdf/install.sh` — pinned checksum-enforcing installer.
- `deploy/verapdf/validate.sh` — version/profile/fixture smoke validation.
- `deploy/verapdf/SHA256SUMS` — exact installer SHA-256.
- `deploy/verapdf/README.md` — operational pin/update/signature-verification runbook.

**Modify**

- `config/settings/base.py` — `UPLOAD_MAX_BYTES`, `VERAPDF_EXECUTABLE`, `VERAPDF_TIMEOUT_SECONDS`, and `OCR_MAX_BYTES = UPLOAD_MAX_BYTES`.
- `.env.example` — runtime settings documentation.
- `apps/documents/models.py` — pre-storage validation of new originals/editables.
- `apps/templates_bank/models.py` — pre-storage validation of new editable/sample files.
- `apps/documents/tests/test_antivirus_integration.py` — isolate antivirus tests from the new format gate.
- `apps/documents/tests/test_macro_check_integration.py` — use structurally valid OOXML before macro markers are added.
- `apps/templates_bank/tests.py` — same antivirus/macro fixture repair.
- `.github/workflows/ci.yml` — install and validate real veraPDF on both DB matrix legs.
- `README.md`, `STACK.md` — intake policy and operational dependency.

---

### Task 1: Shared intake size and OOXML validation

**Files:**
- Create: `apps/core/upload_validation.py`
- Create: `apps/core/tests/test_upload_validation.py`
- Create: `apps/core/tests/upload_fixtures.py`
- Modify: `config/settings/base.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `UploadValidationError`, `UploadTooLarge`, `InvalidOOXML`, `InvalidPDF`, `PDFAValidationFailed`, `FormatValidatorUnavailable`
- Produces: `validate_upload_size(field_file) -> None`
- Produces: `validate_ooxml(field_file, expected_kind: str) -> None`
- Produces test helpers: `docx_bytes(extra=()) -> bytes`, `xlsx_bytes(extra=()) -> bytes`

- [ ] **Step 1: Write the test fixture helpers**

Create `apps/core/tests/upload_fixtures.py`:

```python
import io
import zipfile

CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

DOCX_MAIN = "word/document.xml"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
XLSX_MAIN = "xl/workbook.xml"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"


def package_bytes(main_part: str, main_type: str, *, extra=()) -> bytes:
    buf = io.BytesIO()
    content_types = (
        f'<Types xmlns="{CONTENT_TYPES_NS}">'
        f'<Override PartName="/{main_part}" ContentType="{main_type}"/>'
        "</Types>"
    )
    rels = f'<Relationships xmlns="{RELS_NS}"/>'
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr(main_part, "<root/>")
        for name, payload in extra:
            archive.writestr(name, payload)
    return buf.getvalue()


def docx_bytes(*, extra=()) -> bytes:
    return package_bytes(DOCX_MAIN, DOCX_TYPE, extra=extra)


def xlsx_bytes(*, extra=()) -> bytes:
    return package_bytes(XLSX_MAIN, XLSX_TYPE, extra=extra)
```

- [ ] **Step 2: Write RED size tests**

Create the first section of `apps/core/tests/test_upload_validation.py`:

```python
import io
import zipfile
from unittest import mock

from django.core.files.base import File
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from apps.core import upload_validation
from apps.core.tests.upload_fixtures import (
    CONTENT_TYPES_NS,
    DOCX_MAIN,
    DOCX_TYPE,
    XLSX_MAIN,
    XLSX_TYPE,
    docx_bytes,
    xlsx_bytes,
)


class UploadSizeTests(SimpleTestCase):
    @override_settings(UPLOAD_MAX_BYTES=10)
    def test_exact_limit_passes(self):
        upload_validation.validate_upload_size(
            SimpleUploadedFile("x.docx", b"x" * 10)
        )

    @override_settings(UPLOAD_MAX_BYTES=10)
    def test_one_byte_over_limit_rejects(self):
        with self.assertRaises(upload_validation.UploadTooLarge):
            upload_validation.validate_upload_size(
                SimpleUploadedFile("x.docx", b"x" * 11)
            )

    @override_settings(UPLOAD_MAX_BYTES=10)
    def test_seek_fallback_restores_position(self):
        stream = io.BytesIO(b"12345")
        field = mock.Mock(spec=["size", "file"])
        field.size = None
        field.file = stream
        upload_validation.validate_upload_size(field)
        self.assertEqual(stream.tell(), 0)

    @override_settings(UPLOAD_MAX_BYTES=10)
    def test_unknown_unseekable_size_rejects_fail_closed(self):
        field = mock.Mock(spec=["size", "file"])
        field.size = None
        field.file.seek.side_effect = OSError("not seekable")
        with self.assertRaises(upload_validation.UploadValidationError):
            upload_validation.validate_upload_size(field)
```

- [ ] **Step 3: Run the size tests and verify RED**

Run:

```bash
python manage.py test apps.core.tests.test_upload_validation.UploadSizeTests
```

Expected: import failure because `apps.core.upload_validation` does not exist.

- [ ] **Step 4: Add the shared settings**

In `config/settings/base.py` replace the independent OCR byte constant with:

```python
UPLOAD_MAX_BYTES = int(os.environ.get("UPLOAD_MAX_BYTES", str(150 * 1024 * 1024)))
OCR_MAX_BYTES = UPLOAD_MAX_BYTES
```

In `.env.example` add:

```dotenv
# Unified maximum intake/OCR file size: 150 MiB by default.
UPLOAD_MAX_BYTES=157286400
```

- [ ] **Step 5: Implement the exception hierarchy and size validator**

Start `apps/core/upload_validation.py` with:

```python
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from contextlib import contextmanager
from xml.etree import ElementTree

from django.conf import settings
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


class UploadValidationError(ValidationError):
    pass


class UploadTooLarge(UploadValidationError):
    pass


class InvalidOOXML(UploadValidationError):
    pass


class InvalidPDF(UploadValidationError):
    pass


class PDFAValidationFailed(UploadValidationError):
    pass


class FormatValidatorUnavailable(UploadValidationError):
    pass


def _source_file(field_file):
    return getattr(field_file, "file", field_file)


def validate_upload_size(field_file) -> None:
    limit = settings.UPLOAD_MAX_BYTES
    size = getattr(field_file, "size", None)
    source = _source_file(field_file)
    if size is None:
        try:
            source.seek(0, os.SEEK_END)
            size = source.tell()
        except (AttributeError, OSError, ValueError) as exc:
            raise UploadValidationError(
                "Не удалось определить размер файла; файл не сохранён."
            ) from exc
        finally:
            try:
                source.seek(0)
            except (AttributeError, OSError, ValueError):
                pass
    if size > limit:
        raise UploadTooLarge(
            f"Размер файла превышает допустимые {limit // (1024 * 1024)} МБ."
        )
```

- [ ] **Step 6: Re-run size tests and verify GREEN**

Run:

```bash
python manage.py test apps.core.tests.test_upload_validation.UploadSizeTests
```

Expected: PASS.

- [ ] **Step 7: Write RED OOXML tests**

Append to `apps/core/tests/test_upload_validation.py`:

```python
def _custom_zip(entries) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return buf.getvalue()


def _content_types(main_part: str, main_type: str) -> str:
    return (
        f'<Types xmlns="{CONTENT_TYPES_NS}">'
        f'<Override PartName="/{main_part}" ContentType="{main_type}"/>'
        "</Types>"
    )


class OOXMLValidationTests(SimpleTestCase):
    def test_non_zip_docx_rejects(self):
        upload = SimpleUploadedFile("x.docx", b"not a zip")
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(upload, "docx")

    def test_malformed_zip_xlsx_rejects(self):
        upload = SimpleUploadedFile("x.xlsx", b"PK\x03\x04broken")
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(upload, "xlsx")

    def test_docx_missing_document_xml_rejects(self):
        payload = _custom_zip([
            ("[Content_Types].xml", _content_types(DOCX_MAIN, DOCX_TYPE)),
            ("_rels/.rels", "<Relationships/>")
        ])
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(SimpleUploadedFile("x.docx", payload), "docx")

    def test_xlsx_missing_workbook_xml_rejects(self):
        payload = _custom_zip([
            ("[Content_Types].xml", _content_types(XLSX_MAIN, XLSX_TYPE)),
            ("_rels/.rels", "<Relationships/>")
        ])
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(SimpleUploadedFile("x.xlsx", payload), "xlsx")

    def test_docx_rejects_xlsx_package(self):
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(
                SimpleUploadedFile("x.docx", xlsx_bytes()), "docx"
            )

    def test_xlsx_rejects_docx_package(self):
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(
                SimpleUploadedFile("x.xlsx", docx_bytes()), "xlsx"
            )

    def test_mismatched_content_type_rejects(self):
        payload = _custom_zip([
            ("[Content_Types].xml", _content_types(DOCX_MAIN, XLSX_TYPE)),
            ("_rels/.rels", "<Relationships/>") ,
            (DOCX_MAIN, "<document/>")
        ])
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(SimpleUploadedFile("x.docx", payload), "docx")

    def test_duplicate_main_part_rejects(self):
        payload = _custom_zip([
            ("[Content_Types].xml", _content_types(DOCX_MAIN, DOCX_TYPE)),
            ("_rels/.rels", "<Relationships/>") ,
            (DOCX_MAIN, "<document id='1'/>") ,
            (DOCX_MAIN, "<document id='2'/>")
        ])
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(SimpleUploadedFile("x.docx", payload), "docx")

    def test_valid_docx_and_xlsx_pass(self):
        upload_validation.validate_ooxml(SimpleUploadedFile("x.docx", docx_bytes()), "docx")
        upload_validation.validate_ooxml(SimpleUploadedFile("x.xlsx", xlsx_bytes()), "xlsx")

    def test_stream_position_restored_after_success_and_failure(self):
        valid = SimpleUploadedFile("x.docx", docx_bytes())
        upload_validation.validate_ooxml(valid, "docx")
        self.assertEqual(valid.tell(), 0)
        invalid = SimpleUploadedFile("x.docx", b"bad")
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(invalid, "docx")
        self.assertEqual(invalid.tell(), 0)
```

- [ ] **Step 8: Run OOXML tests and verify RED**

Run:

```bash
python manage.py test apps.core.tests.test_upload_validation.OOXMLValidationTests
```

Expected: failure because `validate_ooxml` is absent.

- [ ] **Step 9: Implement OOXML validation**

Append to `apps/core/upload_validation.py`:

```python
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
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


def validate_ooxml(field_file, expected_kind: str) -> None:
    if expected_kind not in OOXML_CONTRACTS:
        raise InvalidOOXML("Файл не соответствует допустимому формату DOCX/XLSX.")
    main_part, expected_type = OOXML_CONTRACTS[expected_kind]
    source = _source_file(field_file)
    source.seek(0)
    try:
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            for required in ("[Content_Types].xml", "_rels/.rels", main_part):
                if names.count(required) != 1:
                    raise InvalidOOXML("Некорректная структура файла DOCX/XLSX.")
            try:
                content_types = ElementTree.fromstring(archive.read("[Content_Types].xml"))
                ElementTree.fromstring(archive.read("_rels/.rels"))
                archive.read(main_part)
            except (ElementTree.ParseError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
                raise InvalidOOXML("Некорректная структура файла DOCX/XLSX.") from exc
            declared = {
                node.attrib.get("PartName"): node.attrib.get("ContentType")
                for node in content_types.findall(f"{{{CONTENT_TYPES_NS}}}Override")
            }
            if declared.get(f"/{main_part}") != expected_type:
                raise InvalidOOXML("Файл не соответствует заявленному формату DOCX/XLSX.")
    except (zipfile.BadZipFile, OSError) as exc:
        raise InvalidOOXML("Некорректная структура файла DOCX/XLSX.") from exc
    finally:
        source.seek(0)
```

- [ ] **Step 10: Verify Task 1 and commit**

Run:

```bash
python manage.py test apps.core.tests.test_upload_validation.UploadSizeTests apps.core.tests.test_upload_validation.OOXMLValidationTests
python manage.py check
```

Expected: PASS.

Commit:

```bash
git add apps/core/upload_validation.py apps/core/tests/test_upload_validation.py apps/core/tests/upload_fixtures.py config/settings/base.py .env.example
git commit -m "feat: validate upload size and OOXML structure"
```

---

### Task 2: Shared temporary file and ordinary PDF validation

**Files:**
- Modify: `apps/core/upload_validation.py`
- Modify: `apps/core/tests/test_upload_validation.py`

**Interfaces:**
- Produces: `_temporary_upload_path(field_file)` private context manager
- Produces: `_validate_pdf_path(path: str) -> None` private helper
- Produces: `validate_pdf(field_file) -> None`
- Does not perform the size check; model boundaries perform size exactly once before format validation.

- [ ] **Step 1: Write RED temp-file tests**

Append:

```python
import os
from pathlib import Path


class RecordingBytesIO(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class TemporaryUploadTests(SimpleTestCase):
    def test_copy_is_bounded_position_is_restored_and_file_is_removed(self):
        source = RecordingBytesIO(b"x" * (2 * 1024 * 1024 + 10))
        field = mock.Mock(spec=["file"])
        field.file = source
        path_value = None
        with upload_validation._temporary_upload_path(field) as path:
            path_value = path
            self.assertTrue(Path(path).exists())
            self.assertEqual(Path(path).stat().st_size, len(source.getvalue()))
        self.assertEqual(source.tell(), 0)
        self.assertFalse(Path(path_value).exists())
        self.assertTrue(source.read_sizes)
        self.assertNotIn(-1, source.read_sizes)
        self.assertLessEqual(max(source.read_sizes), 1024 * 1024)

    def test_temp_file_is_removed_when_consumer_raises(self):
        source = io.BytesIO(b"payload")
        field = mock.Mock(spec=["file"])
        field.file = source
        path_value = None
        with self.assertRaises(RuntimeError):
            with upload_validation._temporary_upload_path(field) as path:
                path_value = path
                raise RuntimeError("consumer failed")
        self.assertEqual(source.tell(), 0)
        self.assertFalse(os.path.exists(path_value))
```

- [ ] **Step 2: Run temp-file tests and verify RED**

Run:

```bash
python manage.py test apps.core.tests.test_upload_validation.TemporaryUploadTests
```

Expected: failure because `_temporary_upload_path` is absent.

- [ ] **Step 3: Implement the temp-file helper**

Append imports and helper:

```python
@contextmanager
def _temporary_upload_path(field_file):
    source = _source_file(field_file)
    source.seek(0)
    path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="bz-get-upload-", suffix=".bin", delete=False) as tmp:
            path = tmp.name
            shutil.copyfileobj(source, tmp, length=1024 * 1024)
            tmp.flush()
        yield path
    finally:
        try:
            source.seek(0)
        finally:
            if path:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
```

- [ ] **Step 4: Write RED Poppler tests**

Append:

```python
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError, PDFPopplerTimeoutError


class PDFValidationTests(SimpleTestCase):
    @mock.patch("apps.core.upload_validation.pdfinfo_from_path", return_value={"Pages": 1})
    def test_valid_pdf_passes(self, pdfinfo):
        upload_validation.validate_pdf(SimpleUploadedFile("x.pdf", b"pdf"))
        self.assertEqual(pdfinfo.call_count, 1)

    @mock.patch("apps.core.upload_validation.pdfinfo_from_path", return_value={"Pages": 0})
    def test_zero_page_pdf_rejects(self, pdfinfo):
        with self.assertRaises(upload_validation.InvalidPDF):
            upload_validation.validate_pdf(SimpleUploadedFile("x.pdf", b"pdf"))

    @mock.patch("apps.core.upload_validation.pdfinfo_from_path", side_effect=PDFPageCountError("bad"))
    def test_parse_error_is_user_input_failure(self, pdfinfo):
        with self.assertRaises(upload_validation.InvalidPDF):
            upload_validation.validate_pdf(SimpleUploadedFile("x.pdf", b"bad"))

    @mock.patch("apps.core.upload_validation.pdfinfo_from_path", side_effect=PDFInfoNotInstalledError("missing"))
    def test_missing_poppler_is_fail_closed(self, pdfinfo):
        with self.assertRaises(upload_validation.FormatValidatorUnavailable):
            upload_validation.validate_pdf(SimpleUploadedFile("x.pdf", b"pdf"))

    @mock.patch("apps.core.upload_validation.pdfinfo_from_path", side_effect=PDFPopplerTimeoutError("timeout"))
    def test_poppler_timeout_is_fail_closed(self, pdfinfo):
        with self.assertRaises(upload_validation.FormatValidatorUnavailable):
            upload_validation.validate_pdf(SimpleUploadedFile("x.pdf", b"pdf"))
```

- [ ] **Step 5: Run PDF tests and verify RED**

Run:

```bash
python manage.py test apps.core.tests.test_upload_validation.PDFValidationTests
```

Expected: failure because `validate_pdf` is absent.

- [ ] **Step 6: Implement path-level and field-level Poppler validation**

Add:

```python
from pdf2image import pdfinfo_from_path
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError, PDFPopplerTimeoutError


def _validate_pdf_path(path: str) -> None:
    try:
        info = pdfinfo_from_path(path, timeout=settings.OCR_PROCESS_TIMEOUT)
    except PDFPageCountError as exc:
        raise InvalidPDF("Некорректный PDF-файл.") from exc
    except (PDFInfoNotInstalledError, PDFPopplerTimeoutError, OSError) as exc:
        logger.exception("PDF structural validator unavailable")
        raise FormatValidatorUnavailable(
            "Проверка формата временно недоступна; файл не сохранён."
        ) from exc
    if int(info.get("Pages", 0)) < 1:
        raise InvalidPDF("Некорректный PDF-файл.")


def validate_pdf(field_file) -> None:
    with _temporary_upload_path(field_file) as path:
        _validate_pdf_path(path)
```

- [ ] **Step 7: Verify Task 2 and commit**

Run:

```bash
python manage.py test apps.core.tests.test_upload_validation.TemporaryUploadTests apps.core.tests.test_upload_validation.PDFValidationTests
```

Expected: PASS.

Commit:

```bash
git add apps/core/upload_validation.py apps/core/tests/test_upload_validation.py
git commit -m "feat: validate ordinary PDF uploads with Poppler"
```

---

### Task 3: PDF/A-2b validation through veraPDF

**Files:**
- Modify: `apps/core/upload_validation.py`
- Modify: `apps/core/tests/test_upload_validation.py`
- Modify: `config/settings/base.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `_temporary_upload_path`, `_validate_pdf_path`
- Produces: `validate_pdfa_2b(field_file) -> None`
- Uses the same temp file for Poppler structural parsing and veraPDF conformance validation.
- Does not perform the size check; model boundaries perform size exactly once first.

- [ ] **Step 1: Add veraPDF runtime settings**

In `config/settings/base.py`:

```python
VERAPDF_EXECUTABLE = os.environ.get("VERAPDF_EXECUTABLE", "verapdf")
VERAPDF_TIMEOUT_SECONDS = int(os.environ.get("VERAPDF_TIMEOUT_SECONDS", "60"))
```

In `.env.example`:

```dotenv
VERAPDF_EXECUTABLE=verapdf
VERAPDF_TIMEOUT_SECONDS=60
```

- [ ] **Step 2: Write RED veraPDF tests**

Append:

```python
class PDFAValidationTests(SimpleTestCase):
    @mock.patch("apps.core.upload_validation.subprocess.run")
    @mock.patch("apps.core.upload_validation._validate_pdf_path")
    def test_poppler_runs_before_verapdf_and_exit_zero_passes(self, validate_path, run):
        events = []
        validate_path.side_effect = lambda path: events.append("poppler")
        run.side_effect = lambda *args, **kwargs: (
            events.append("verapdf") or mock.Mock(returncode=0, stdout="", stderr="")
        )
        upload_validation.validate_pdfa_2b(SimpleUploadedFile("x.pdf", b"pdf"))
        self.assertEqual(events, ["poppler", "verapdf"])
        command = run.call_args.args[0]
        self.assertEqual(command[0], settings.VERAPDF_EXECUTABLE)
        self.assertEqual(command[1:10], [
            "--format", "text",
            "--maxfailures", "1",
            "--maxfailuresdisplayed", "1",
            "--loglevel", "1",
            "-f", "2b",
        ])
        self.assertFalse(run.call_args.kwargs["shell"])

    @mock.patch("apps.core.upload_validation.subprocess.run", return_value=mock.Mock(returncode=1, stdout="", stderr="non-compliant"))
    @mock.patch("apps.core.upload_validation._validate_pdf_path")
    def test_exit_one_is_pdfa_nonconformance(self, validate_path, run):
        with self.assertRaises(upload_validation.PDFAValidationFailed):
            upload_validation.validate_pdfa_2b(SimpleUploadedFile("x.pdf", b"pdf"))

    @mock.patch("apps.core.upload_validation.subprocess.run", return_value=mock.Mock(returncode=2, stdout="", stderr="internal details"))
    @mock.patch("apps.core.upload_validation._validate_pdf_path")
    def test_unexpected_exit_is_fail_closed_without_diagnostic_leak(self, validate_path, run):
        with self.assertRaises(upload_validation.FormatValidatorUnavailable) as caught:
            upload_validation.validate_pdfa_2b(SimpleUploadedFile("x.pdf", b"pdf"))
        self.assertNotIn("internal details", str(caught.exception))

    @mock.patch("apps.core.upload_validation.subprocess.run", side_effect=FileNotFoundError("verapdf missing"))
    @mock.patch("apps.core.upload_validation._validate_pdf_path")
    def test_missing_executable_is_fail_closed(self, validate_path, run):
        with self.assertRaises(upload_validation.FormatValidatorUnavailable):
            upload_validation.validate_pdfa_2b(SimpleUploadedFile("x.pdf", b"pdf"))

    @mock.patch("apps.core.upload_validation.subprocess.run", side_effect=subprocess.TimeoutExpired("verapdf", 60))
    @mock.patch("apps.core.upload_validation._validate_pdf_path")
    def test_timeout_is_fail_closed(self, validate_path, run):
        with self.assertRaises(upload_validation.FormatValidatorUnavailable):
            upload_validation.validate_pdfa_2b(SimpleUploadedFile("x.pdf", b"pdf"))
```

- [ ] **Step 3: Run PDF/A tests and verify RED**

Run:

```bash
python manage.py test apps.core.tests.test_upload_validation.PDFAValidationTests
```

Expected: failure because `validate_pdfa_2b` is absent.

- [ ] **Step 4: Implement `validate_pdfa_2b`**

Add:

```python
def validate_pdfa_2b(field_file) -> None:
    with _temporary_upload_path(field_file) as path:
        _validate_pdf_path(path)
        command = [
            settings.VERAPDF_EXECUTABLE,
            "--format", "text",
            "--maxfailures", "1",
            "--maxfailuresdisplayed", "1",
            "--loglevel", "1",
            "-f", "2b",
            path,
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=settings.VERAPDF_TIMEOUT_SECONDS,
                shell=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            logger.exception("veraPDF validator unavailable")
            raise FormatValidatorUnavailable(
                "Проверка формата временно недоступна; файл не сохранён."
            ) from exc
        if completed.returncode == 0:
            return
        if completed.returncode == 1:
            raise PDFAValidationFailed("Файл не соответствует профилю PDF/A-2b.")
        diagnostic = (completed.stderr or completed.stdout or "")[:2000]
        logger.error("veraPDF runtime failure rc=%s diagnostic=%r", completed.returncode, diagnostic)
        raise FormatValidatorUnavailable(
            "Проверка формата временно недоступна; файл не сохранён."
        )
```

- [ ] **Step 5: Verify Task 3 and commit**

Run:

```bash
python manage.py test apps.core.tests.test_upload_validation.PDFAValidationTests
python manage.py check
```

Expected: PASS.

Commit:

```bash
git add apps/core/upload_validation.py apps/core/tests/test_upload_validation.py config/settings/base.py .env.example
git commit -m "feat: validate PDF-A-2b uploads with veraPDF"
```

---

### Task 4: `NormativeDocument` boundary and existing antivirus/macro regressions

**Files:**
- Modify: `apps/documents/models.py`
- Create: `apps/documents/tests/test_upload_format_validation.py`
- Modify: `apps/documents/tests/test_antivirus_integration.py`
- Modify: `apps/documents/tests/test_macro_check_integration.py`

**Interfaces:**
- Consumes: `validate_upload_size`, `validate_pdfa_2b`, `validate_ooxml`
- Preserves: `antivirus.needs_scan`, `scan_uploaded_field`, `macro_check.reject_if_has_macros`

- [ ] **Step 1: Write RED document-boundary tests**

Create `apps/documents/tests/test_upload_format_validation.py`:

```python
import datetime
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.core import upload_validation
from apps.documents.models import NormativeDocument
from apps.documents.retention import RetentionCategory
from apps.iam.models import Department


class DocumentUploadFormatTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Format validation", level=Department.Level.SERVICE)

    def build(self, *, original=None, editable=None):
        return NormativeDocument(
            reg_number="FMT-1",
            reg_date=datetime.date(2026, 1, 1),
            effective_date=datetime.date(2026, 1, 2),
            doc_type=NormativeDocument.DocType.ORDER,
            title="Format validation",
            issuer_dept=self.dept,
            retention_category=RetentionCategory.ORDERS_CORE,
            files_original=original or "documents/originals/existing.pdf",
            files_editable=editable,
        )

    @mock.patch("apps.core.staged_files.stage_uploaded_field")
    @mock.patch("apps.documents.models.antivirus.scan_uploaded_field")
    @mock.patch("apps.documents.models.upload_validation.validate_pdfa_2b", side_effect=upload_validation.PDFAValidationFailed("bad"))
    def test_original_rejects_before_antivirus_and_staging(self, pdfa, antivirus, stage):
        doc = self.build(original=SimpleUploadedFile("scan.pdf", b"ordinary pdf"))
        with self.assertRaises(upload_validation.PDFAValidationFailed):
            doc.save()
        antivirus.assert_not_called()
        stage.assert_not_called()
        self.assertFalse(NormativeDocument.objects.filter(reg_number="FMT-1").exists())

    @mock.patch("apps.documents.models.antivirus.scan_uploaded_field")
    @mock.patch("apps.documents.models.upload_validation.validate_ooxml", side_effect=upload_validation.InvalidOOXML("bad"))
    def test_editable_rejects_before_antivirus_and_mutable_storage(self, ooxml, antivirus):
        doc = self.build(editable=SimpleUploadedFile("draft.docx", b"bad"))
        with mock.patch.object(doc.files_editable.storage, "save") as storage_save:
            with self.assertRaises(upload_validation.InvalidOOXML):
                doc.save()
        antivirus.assert_not_called()
        storage_save.assert_not_called()

    @mock.patch.object(NormativeDocument, "_save_validated")
    @mock.patch("apps.documents.models.macro_check.reject_if_has_macros")
    @mock.patch("apps.documents.models.antivirus.scan_uploaded_field")
    @mock.patch("apps.documents.models.upload_validation.validate_ooxml")
    @mock.patch("apps.documents.models.upload_validation.validate_upload_size")
    def test_editable_order_is_size_format_antivirus_macro_save(self, size, ooxml, antivirus, macro, save):
        events = []
        size.side_effect = lambda field: events.append("size")
        ooxml.side_effect = lambda field, kind: events.append("format")
        antivirus.side_effect = lambda *args, **kwargs: events.append("antivirus")
        macro.side_effect = lambda *args, **kwargs: events.append("macro")
        save.side_effect = lambda *args, **kwargs: events.append("save")
        self.build(editable=SimpleUploadedFile("draft.docx", b"payload")).save()
        self.assertEqual(events, ["size", "format", "antivirus", "macro", "save"])

    @mock.patch.object(NormativeDocument, "_save_validated")
    @mock.patch("apps.documents.models.antivirus.scan_uploaded_field")
    @mock.patch("apps.documents.models.upload_validation.validate_pdfa_2b")
    @mock.patch("apps.documents.models.upload_validation.validate_upload_size")
    def test_original_order_is_size_pdfa_antivirus_save(self, size, pdfa, antivirus, save):
        events = []
        size.side_effect = lambda field: events.append("size")
        pdfa.side_effect = lambda field: events.append("pdfa")
        antivirus.side_effect = lambda *args, **kwargs: events.append("antivirus")
        save.side_effect = lambda *args, **kwargs: events.append("save")
        self.build(original=SimpleUploadedFile("scan.pdf", b"payload")).save()
        self.assertEqual(events, ["size", "pdfa", "antivirus", "save"])

    @mock.patch("apps.documents.models.upload_validation.validate_upload_size")
    def test_existing_committed_names_are_not_revalidated(self, size):
        doc = self.build()
        with mock.patch.object(NormativeDocument, "_save_validated"):
            doc.save()
        size.assert_not_called()
```

- [ ] **Step 2: Run document boundary tests and verify RED**

Run:

```bash
python manage.py test apps.documents.tests.test_upload_format_validation
```

Expected: failure because `apps.documents.models` does not import/call `upload_validation`.

- [ ] **Step 3: Implement the document validation order**

In `apps/documents/models.py` import `Path` and `upload_validation`, then change the new-upload block to:

```python
for field_name in ("files_original", "files_editable"):
    field_file = getattr(self, field_name)
    if antivirus.needs_scan(field_file):
        upload_validation.validate_upload_size(field_file)
        if field_name == "files_original":
            upload_validation.validate_pdfa_2b(field_file)
        else:
            expected_kind = Path(field_file.name).suffix.lower().lstrip(".")
            upload_validation.validate_ooxml(field_file, expected_kind)

        antivirus.scan_uploaded_field(
            field_file,
            object_type="NormativeDocument",
            object_id=str(self.pk),
            object_label=self.reg_number,
        )
        if field_name == "files_editable":
            macro_check.reject_if_has_macros(
                field_file,
                object_type="NormativeDocument",
                object_id=str(self.pk),
                object_label=self.reg_number,
            )
```

- [ ] **Step 4: Repair existing antivirus and macro tests so they test their own layer**

In `apps/documents/tests/test_antivirus_integration.py`, patch `apps.documents.models.upload_validation.validate_pdfa_2b` or `validate_ooxml` in tests that intentionally feed EICAR bytes that are not valid PDF/OOXML. Keep live clamd assertions unchanged.

In `apps/documents/tests/test_macro_check_integration.py`, replace hand-built malformed ZIP packages with `apps.core.tests.upload_fixtures.docx_bytes(extra=(("word/vbaProject.bin", b"fake vba bytecode"),))` so macro detection is reached after structural validation.

- [ ] **Step 5: Verify Task 4 and commit**

Run:

```bash
python manage.py test \
  apps.documents.tests.test_upload_format_validation \
  apps.documents.tests.test_antivirus_integration \
  apps.documents.tests.test_macro_check_integration
```

Expected: PASS.

Commit:

```bash
git add apps/documents/models.py apps/documents/tests/test_upload_format_validation.py apps/documents/tests/test_antivirus_integration.py apps/documents/tests/test_macro_check_integration.py
git commit -m "feat: enforce upload validation on documents"
```

---

### Task 5: `Template` boundary and existing template regressions

**Files:**
- Modify: `apps/templates_bank/models.py`
- Create: `apps/templates_bank/test_upload_format_validation.py`
- Modify: `apps/templates_bank/tests.py`

**Interfaces:**
- Consumes: `validate_upload_size`, `validate_ooxml`, `validate_pdf`
- Preserves: template staging/WORM promotion, ClamAV, macro audit, status audit.

- [ ] **Step 1: Write RED template-boundary tests**

Create `apps/templates_bank/test_upload_format_validation.py`:

```python
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.core import upload_validation
from apps.documents.tests.factories import make_document
from apps.templates_bank.models import Template, TemplateFamily


class TemplateUploadFormatTests(TestCase):
    def setUp(self):
        self.family = TemplateFamily.objects.create(name="Format validation")
        self.approving = make_document(reg_number="FMT-TEMPLATE")

    def build(self, *, editable=None, sample=None):
        return Template(
            family=self.family,
            version="v1.0",
            change_type=Template.ChangeType.MAJOR,
            approving_document=self.approving,
            file_editable=editable or "templates/editable/existing.docx",
            file_sample=sample or "templates/samples/existing.pdf",
        )

    @mock.patch("apps.core.staged_files.stage_uploaded_field")
    @mock.patch("apps.templates_bank.models.antivirus.scan_uploaded_field")
    @mock.patch("apps.templates_bank.models.upload_validation.validate_ooxml", side_effect=upload_validation.InvalidOOXML("bad"))
    def test_editable_rejects_before_antivirus_and_staging(self, ooxml, antivirus, stage):
        template = self.build(editable=SimpleUploadedFile("form.docx", b"bad"))
        with self.assertRaises(upload_validation.InvalidOOXML):
            template.save()
        antivirus.assert_not_called()
        stage.assert_not_called()

    @mock.patch("apps.core.staged_files.stage_uploaded_field")
    @mock.patch("apps.templates_bank.models.antivirus.scan_uploaded_field")
    @mock.patch("apps.templates_bank.models.upload_validation.validate_pdf", side_effect=upload_validation.InvalidPDF("bad"))
    def test_sample_rejects_before_antivirus_and_staging(self, pdf, antivirus, stage):
        template = self.build(sample=SimpleUploadedFile("sample.pdf", b"bad"))
        with self.assertRaises(upload_validation.InvalidPDF):
            template.save()
        antivirus.assert_not_called()
        stage.assert_not_called()

    @mock.patch("apps.templates_bank.models.macro_check.reject_if_has_macros")
    @mock.patch("apps.templates_bank.models.antivirus.scan_uploaded_field")
    @mock.patch("apps.templates_bank.models.upload_validation.validate_ooxml")
    @mock.patch("apps.templates_bank.models.upload_validation.validate_upload_size")
    @mock.patch("django.db.models.Model.save")
    def test_editable_order_is_size_format_antivirus_macro_save(self, base_save, size, ooxml, antivirus, macro):
        events = []
        size.side_effect = lambda field: events.append("size")
        ooxml.side_effect = lambda field, kind: events.append("format")
        antivirus.side_effect = lambda *args, **kwargs: events.append("antivirus")
        macro.side_effect = lambda *args, **kwargs: events.append("macro")
        base_save.side_effect = lambda *args, **kwargs: events.append("save")
        self.build(editable=SimpleUploadedFile("form.docx", b"payload")).save()
        self.assertEqual(events[:5], ["size", "format", "antivirus", "macro", "save"])

    @mock.patch("apps.templates_bank.models.antivirus.scan_uploaded_field")
    @mock.patch("apps.templates_bank.models.upload_validation.validate_pdf")
    @mock.patch("apps.templates_bank.models.upload_validation.validate_upload_size")
    @mock.patch("django.db.models.Model.save")
    def test_sample_order_is_size_pdf_antivirus_save(self, base_save, size, pdf, antivirus):
        events = []
        size.side_effect = lambda field: events.append("size")
        pdf.side_effect = lambda field: events.append("pdf")
        antivirus.side_effect = lambda *args, **kwargs: events.append("antivirus")
        base_save.side_effect = lambda *args, **kwargs: events.append("save")
        self.build(sample=SimpleUploadedFile("sample.pdf", b"payload")).save()
        self.assertEqual(events[:4], ["size", "pdf", "antivirus", "save"])

    @mock.patch("apps.templates_bank.models.upload_validation.validate_upload_size")
    def test_existing_committed_names_are_not_revalidated(self, size):
        with mock.patch("django.db.models.Model.save"):
            self.build().save()
        size.assert_not_called()
```

- [ ] **Step 2: Run template boundary tests and verify RED**

Run:

```bash
python manage.py test apps.templates_bank.test_upload_format_validation
```

Expected: failure because `Template.save()` does not call `upload_validation`.

- [ ] **Step 3: Implement template validation order**

In `apps/templates_bank/models.py` import `Path` and `upload_validation`, then inside the existing new-upload guard use:

```python
upload_validation.validate_upload_size(field_file)
if field_name == "file_editable":
    expected_kind = Path(field_file.name).suffix.lower().lstrip(".")
    upload_validation.validate_ooxml(field_file, expected_kind)
else:
    upload_validation.validate_pdf(field_file)

antivirus.scan_uploaded_field(
    field_file,
    object_type="Template",
    object_id=str(self.pk),
    object_label=label,
)
if field_name == "file_editable":
    macro_check.reject_if_has_macros(
        field_file,
        object_type="Template",
        object_id=str(self.pk),
        object_label=label,
    )
```

- [ ] **Step 4: Repair template antivirus/macro fixture isolation**

In `apps/templates_bank/tests.py` replace the current malformed `_docx_bytes()` implementation with `apps.core.tests.upload_fixtures.docx_bytes()`. For macro tests, pass `extra=(("word/vbaProject.bin", b"fake vba bytecode"),)`. For EICAR tests that intentionally use raw EICAR bytes, patch only the new format validator so live ClamAV remains the layer under test.

- [ ] **Step 5: Verify Task 5 and commit**

Run:

```bash
python manage.py test apps.templates_bank.test_upload_format_validation apps.templates_bank.tests
```

Expected: PASS.

Commit:

```bash
git add apps/templates_bank/models.py apps/templates_bank/test_upload_format_validation.py apps/templates_bank/tests.py
git commit -m "feat: enforce upload validation on templates"
```

---

### Task 6: Pin veraPDF 1.30.2 and add real Poppler/veraPDF smoke checks

**Files:**
- Create: `apps/core/tests/fixtures/pdfa-2b-valid.pdf`
- Create: `apps/core/tests/fixtures/pdfa-2b-invalid.pdf`
- Create: `apps/core/tests/fixtures/README.md`
- Create: `deploy/verapdf/auto-install.xml`
- Create: `deploy/verapdf/install.sh`
- Create: `deploy/verapdf/validate.sh`
- Create: `deploy/verapdf/SHA256SUMS`
- Create: `deploy/verapdf/README.md`
- Modify: `apps/core/tests/test_upload_validation.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Installer artifact: `https://software.verapdf.org/rel/1.30/verapdf-greenfield-1.30.2-installer.zip`
- Detached signature: same URL plus `.asc`
- Official signing fingerprint: `13DD 102B 4DD6 9354 D12D E5A8 3184 8632 78B1 7FE7`
- Produces default executable: `/opt/verapdf/verapdf`

- [ ] **Step 1: Vendor exact upstream corpus fixtures**

Use these exact veraPDF-corpus `master` objects:

```text
PASS source:
PDF_A-2b/6.1 File structure/6.1.2 File header/veraPDF test suite 6-1-2-t02-pass-a.pdf
Git blob: 1bad81119b6bbf2cb52d2713f57955bf32bbe749
Destination: apps/core/tests/fixtures/pdfa-2b-valid.pdf

FAIL source:
PDF_A-2b/6.1 File structure/6.1.2 File header/veraPDF test suite 6-1-2-t02-fail-a.pdf
Git blob: f01744ef9a17e5a6442a5de7644f6d251e7cf879
Destination: apps/core/tests/fixtures/pdfa-2b-invalid.pdf
```

`apps/core/tests/fixtures/README.md` records repository, original path, blob SHA, retrieval date `2026-09-12`, and veraPDF-corpus CC BY 4.0 licensing.

- [ ] **Step 2: Verify upstream signature once and commit the exact installer SHA-256**

Download the release ZIP and `.asc`, import the official signing key, verify its fingerprint, verify the detached signature, then compute the digest:

```bash
gpg --fingerprint 78B17FE7
gpg --verify verapdf-greenfield-1.30.2-installer.zip.asc verapdf-greenfield-1.30.2-installer.zip
sha256sum verapdf-greenfield-1.30.2-installer.zip > deploy/verapdf/SHA256SUMS
```

The observed fingerprint must be exactly:

```text
13DD 102B 4DD6 9354 D12D E5A8 3184 8632 78B1 7FE7
```

Do not commit `SHA256SUMS` unless it contains a real 64-hex digest and exact archive filename. Runtime installation uses this committed digest and does not contact a keyserver.

- [ ] **Step 3: Create the exact IzPack descriptor**

Create `deploy/verapdf/auto-install.xml` from upstream CLI pack metadata:

```xml
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
    <com.izforge.izpack.panels.htmlhello.HTMLHelloPanel id="welcome"/>
    <com.izforge.izpack.panels.target.TargetPanel id="install_dir">
        <installpath>/opt/verapdf</installpath>
    </com.izforge.izpack.panels.target.TargetPanel>
    <com.izforge.izpack.panels.packs.PacksPanel id="sdk_pack_select">
        <pack index="0" name="veraPDF GUI" selected="false"/>
        <pack index="1" name="veraPDF CLI" selected="true"/>
        <pack index="2" name="veraPDF Documentation" selected="false"/>
        <pack index="3" name="veraPDF Sample Plugins" selected="false"/>
    </com.izforge.izpack.panels.packs.PacksPanel>
    <com.izforge.izpack.panels.install.InstallPanel id="install"/>
    <com.izforge.izpack.panels.finish.FinishPanel id="finish"/>
</AutomatedInstallation>
```

Keep `/opt/verapdf` as the production/CI install root; do not parameterize the XML in this change.

- [ ] **Step 4: Create checksum-enforcing `deploy/verapdf/install.sh`**

The script starts with:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="1.30.2"
ARCHIVE="verapdf-greenfield-${VERSION}-installer.zip"
BASE_URL="https://software.verapdf.org/rel/1.30"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cd "$WORK"
curl -fsSLO "$BASE_URL/$ARCHIVE"
grep "  $ARCHIVE$" "$ROOT/deploy/verapdf/SHA256SUMS" | sha256sum -c -
unzip -q "$ARCHIVE"
java -jar "verapdf-greenfield-${VERSION}/verapdf-izpack-installer-${VERSION}.jar" \
  "$ROOT/deploy/verapdf/auto-install.xml"
test -x /opt/verapdf/verapdf
/opt/verapdf/verapdf --version | grep -F "1.30.2"
```

The script has no “latest” fallback and exits non-zero on checksum/version mismatch.

- [ ] **Step 5: Create `deploy/verapdf/validate.sh`**

Use:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERAPDF="${1:-/opt/verapdf/verapdf}"
PASS="$ROOT/apps/core/tests/fixtures/pdfa-2b-valid.pdf"
FAIL="$ROOT/apps/core/tests/fixtures/pdfa-2b-invalid.pdf"

"$VERAPDF" --version | grep -F "1.30.2"
"$VERAPDF" -l | grep -F "2b"
pdfinfo "$PASS" >/dev/null
pdfinfo "$FAIL" >/dev/null
"$VERAPDF" -f 2b "$PASS"
set +e
"$VERAPDF" -f 2b "$FAIL"
rc=$?
set -e
[[ "$rc" -eq 1 ]]
```

- [ ] **Step 6: Add real-runtime Django smoke tests**

Append to `apps/core/tests/test_upload_validation.py`:

```python
import shutil
from pathlib import Path

FIXTURES = Path(__file__).with_name("fixtures")


class RealValidatorSmokeTests(SimpleTestCase):
    def setUp(self):
        if shutil.which(settings.VERAPDF_EXECUTABLE) is None:
            self.skipTest("veraPDF executable is not installed outside CI/runtime smoke")

    def test_pdfa_pass_fixture_passes_verapdf(self):
        with open(FIXTURES / "pdfa-2b-valid.pdf", "rb") as handle:
            upload_validation.validate_pdfa_2b(File(handle, name="valid.pdf"))

    def test_pdfa_fail_fixture_is_rejected(self):
        with open(FIXTURES / "pdfa-2b-invalid.pdf", "rb") as handle:
            with self.assertRaises(upload_validation.PDFAValidationFailed):
                upload_validation.validate_pdfa_2b(File(handle, name="invalid.pdf"))

    def test_poppler_parses_both_checked_in_pdfs(self):
        for name in ("pdfa-2b-valid.pdf", "pdfa-2b-invalid.pdf"):
            with self.subTest(name=name):
                with open(FIXTURES / name, "rb") as handle:
                    upload_validation.validate_pdf(File(handle, name=name))
```

- [ ] **Step 7: Wire installation and smoke validation into both CI matrix legs**

In `.github/workflows/ci.yml`, after the OCR system-package step add:

```yaml
- name: Установить veraPDF 1.30.2
  run: |
    sudo apt-get install -y -qq default-jre-headless unzip curl
    sudo bash deploy/verapdf/install.sh
    echo "/opt/verapdf" >> "$GITHUB_PATH"

- name: Проверить veraPDF/Poppler intake validators
  run: |
    bash -n deploy/verapdf/install.sh
    bash -n deploy/verapdf/validate.sh
    bash deploy/verapdf/validate.sh /opt/verapdf/verapdf
```

The existing job matrix automatically executes both steps on PostgreSQL 16 and 18.

- [ ] **Step 8: Verify deployment/runtime task and commit**

Run:

```bash
bash -n deploy/verapdf/install.sh
bash -n deploy/verapdf/validate.sh
sudo apt-get install -y default-jre-headless unzip curl
sudo bash deploy/verapdf/install.sh
bash deploy/verapdf/validate.sh /opt/verapdf/verapdf
VERAPDF_EXECUTABLE=/opt/verapdf/verapdf python manage.py test apps.core.tests.test_upload_validation.RealValidatorSmokeTests
```

Expected: all commands PASS; fail fixture is accepted by Poppler and rejected by veraPDF with exit code 1.

Commit:

```bash
git add apps/core/tests/fixtures apps/core/tests/test_upload_validation.py deploy/verapdf .github/workflows/ci.yml
git commit -m "build: pin veraPDF and validate PDF-A runtime"
```

---

### Task 7: Final invariant checks, docs, and full CI-equivalent verification

**Files:**
- Modify: `apps/core/tests/test_production_settings.py`
- Modify: `README.md`
- Modify: `STACK.md`
- Review: all files changed by Tasks 1–6

**Interfaces:**
- No new runtime interfaces.
- Locks the accepted behavior in tests and operational docs.

- [ ] **Step 1: Add the shared-limit settings regression**

In `apps/core/tests/test_production_settings.py` add:

```python
from django.conf import settings


def test_upload_and_ocr_limits_match(self):
    self.assertEqual(settings.UPLOAD_MAX_BYTES, 150 * 1024 * 1024)
    self.assertEqual(settings.OCR_MAX_BYTES, settings.UPLOAD_MAX_BYTES)
```

If that file uses a different test class style, place the method on the existing settings test class without changing its base class.

- [ ] **Step 2: Update operational documentation**

`README.md` records:

```text
Upload intake policy
- Maximum size: 150 MiB.
- NРД originals: PDF/A-2b only.
- Editable NРД/template files: real DOCX/XLSX OOXML only, macros/ActiveX prohibited.
- Template sample: valid ordinary PDF; PDF/A is not required.
- Poppler/veraPDF validation is fail-closed and runs before storage/WORM staging.
```

`STACK.md` records veraPDF Greenfield 1.30.2, fixed `2b` flavour, checksum pin location `deploy/verapdf/SHA256SUMS`, install/upgrade runbook `deploy/verapdf/README.md`, and exact validation order.

- [ ] **Step 3: Run the focused upload/antivirus/macro regression set**

Run:

```bash
python manage.py test \
  apps.core.tests.test_upload_validation \
  apps.documents.tests.test_upload_format_validation \
  apps.documents.tests.test_antivirus_integration \
  apps.documents.tests.test_macro_check_integration \
  apps.templates_bank.test_upload_format_validation \
  apps.templates_bank.tests
```

Expected: zero failures/errors.

- [ ] **Step 4: Run configuration and migration checks**

Run:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
bash -n deploy/verapdf/install.sh
bash -n deploy/verapdf/validate.sh
bash deploy/verapdf/validate.sh /opt/verapdf/verapdf
```

Expected: all commands exit 0 and Django reports no model changes.

- [ ] **Step 5: Run the exact full project verification used by CI**

Run in the CI-equivalent PostgreSQL/Redis environment:

```bash
python manage.py migrate
coverage run manage.py test
coverage report --fail-under=85
coverage report --include="apps/iam/security.py,apps/iam/middleware.py,apps/core/outbox.py" --fail-under=90
celery -A config worker --pool=solo --loglevel=INFO > /tmp/bz-get-worker.log 2>&1 &
worker_pid=$!
trap 'kill "$worker_pid" || true' EXIT
python manage.py verify_async_delivery
```

Expected: full suite PASS, both coverage gates PASS, and live Redis/worker integration PASS.

- [ ] **Step 6: Review every spec acceptance criterion before the final commit**

Verify by test name or command evidence that:

1. 150 MiB is accepted and 150 MiB + 1 byte is rejected;
2. arbitrary renamed bytes and malformed ZIPs are rejected;
3. DOCX/XLSX type mismatch is rejected;
4. `files_original` accepts only PDF/A-2b;
5. a structurally valid PDF/A-2b fail fixture is rejected for `files_original`;
6. `Template.file_sample` remains ordinary-PDF only;
7. missing/time-out Poppler and veraPDF reject fail-closed;
8. failed validation reaches neither mutable storage nor staged WORM upload;
9. ClamAV/macro tests still reach and prove their own layers;
10. real veraPDF/Poppler smoke runs in both matrix jobs;
11. no migration or new audit event appears;
12. README/STACK/deploy runbook record the pin and policy.

- [ ] **Step 7: Commit final regressions/docs**

```bash
git add apps/core/tests/test_production_settings.py README.md STACK.md
git commit -m "docs: record pre-WORM upload validation policy"
```

- [ ] **Step 8: Open implementation PR as draft and require fresh GitHub evidence before merge**

The PR body includes:

```text
Spec: docs/superpowers/specs/2026-09-12-upload-validation-design.md
Plan: docs/superpowers/plans/2026-09-12-upload-validation-implementation.md
TDD: RED evidence recorded before each production change; GREEN focused tests recorded after each task.
Runtime: real veraPDF 1.30.2 + Poppler smoke enabled on PostgreSQL 16/18 CI legs.
Schema: no migration introduced.
```

Do not mark ready or merge until current-head `Quality` is success, both current-head PostgreSQL 16/18 CI jobs are success including real Redis/worker integration, `deploy/verapdf/validate.sh` passed in both jobs, and review threads are empty or resolved.
