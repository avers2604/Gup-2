# Pre-WORM PDF/A and OOXML Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce a 150 MiB intake limit and validate real OOXML/PDF/PDF-A content before any upload can reach mutable storage, staging, or WORM promotion.

**Architecture:** Add a focused `apps/core/upload_validation.py` boundary with independent size, OOXML, Poppler-PDF, and veraPDF-PDF/A validators. Existing model `save()` methods invoke that boundary only for new uncommitted uploads, then continue through existing ClamAV/macro checks and storage/staging logic. veraPDF is installed locally as a pinned runtime dependency and exercised by a real CI smoke test.

**Tech Stack:** Django 5.2, Python 3.13, `zipfile`, `xml.etree.ElementTree`, `tempfile`, `subprocess`, `pdf2image.pdfinfo_from_path`/Poppler, veraPDF Greenfield 1.30.2, GitHub Actions, PostgreSQL 16/18.

**Spec:** `docs/superpowers/specs/2026-09-12-upload-validation-design.md`

## Global Constraints

- Intake limit is exactly `150 * 1024 * 1024` bytes; exactly 150 MiB passes, one byte more fails.
- `NormativeDocument.files_original` accepts only PDF/A-2b.
- `NormativeDocument.files_editable` and `Template.file_editable` accept only structurally valid OOXML matching `.docx`/`.xlsx`.
- `Template.file_sample` requires a structurally valid ordinary PDF, not PDF/A.
- PDF/A policy is fixed to veraPDF flavour `2b`; it is not configurable.
- veraPDF production pin is Greenfield 1.30.2.
- Missing/failed/timed-out Poppler or veraPDF is fail-closed.
- Validation runs before ClamAV, macro detection, `FileField.pre_save()`, staging, or WORM promotion.
- Existing stored file names are not revalidated on unrelated saves; use `antivirus.needs_scan(field_file)` as the existing new-upload guard.
- No new database schema and no new audit event family are introduced.
- Existing malware and macro audit semantics remain unchanged.

---

## File Map

**Create**

- `apps/core/upload_validation.py` — all intake content validators and their exceptions.
- `apps/core/tests/test_upload_validation.py` — deterministic unit tests for size, OOXML, Poppler wrapper, veraPDF wrapper, temp-file cleanup and stream restoration.
- `apps/core/tests/upload_fixtures.py` — reusable builders for valid minimal DOCX/XLSX and helpers to load checked-in PDF fixtures.
- `apps/core/tests/fixtures/pdfa-2b-valid.pdf` — small upstream veraPDF-corpus PDF/A-2b pass fixture.
- `apps/core/tests/fixtures/pdfa-2b-invalid.pdf` — valid PDF that fails PDF/A-2b.
- `apps/core/tests/fixtures/README.md` — exact provenance/licensing for both PDF fixtures.
- `apps/documents/tests/test_upload_format_validation.py` — document model boundary and no-storage-side-effect regressions.
- `apps/templates_bank/test_upload_format_validation.py` — template model boundary and no-storage-side-effect regressions.
- `deploy/verapdf/auto-install.xml` — non-interactive IzPack install descriptor.
- `deploy/verapdf/install.sh` — pinned/checksummed veraPDF installer.
- `deploy/verapdf/validate.sh` — runtime/version/profile/fixture smoke validation.
- `deploy/verapdf/SHA256SUMS` — repository-maintained checksum for the pinned installer archive.
- `deploy/verapdf/README.md` — pin/update/signature-verification runbook.

**Modify**

- `config/settings/base.py` — add `UPLOAD_MAX_BYTES`, `VERAPDF_EXECUTABLE`, `VERAPDF_TIMEOUT_SECONDS`; make `OCR_MAX_BYTES = UPLOAD_MAX_BYTES`.
- `.env.example` — document the new runtime settings.
- `apps/documents/models.py` — validate new originals/editables before antivirus/macro/storage work.
- `apps/templates_bank/models.py` — validate new editable/sample uploads before antivirus/macro/storage work.
- `apps/documents/tests/test_antivirus_integration.py` — replace fake-format “clean” payloads with structurally valid fixture payloads so antivirus assertions still test antivirus.
- `apps/documents/tests/test_macro_check_integration.py` — make generated DOCX structurally valid before macro markers are added.
- `apps/templates_bank/tests.py` — same fixture correction for template antivirus/macro tests.
- `.github/workflows/ci.yml` — install Java/unzip, install pinned veraPDF, run `deploy/verapdf/validate.sh`, then Django tests on both PostgreSQL matrix legs.
- `README.md` and `STACK.md` — record intake format policy and veraPDF operational dependency.

---

### Task 1: Introduce the shared size limit and OOXML validator

**Files:**
- Create: `apps/core/upload_validation.py`
- Create: `apps/core/tests/test_upload_validation.py`
- Create: `apps/core/tests/upload_fixtures.py`
- Modify: `config/settings/base.py` around the current `OCR_MAX_BYTES` block
- Modify: `.env.example` near OCR/runtime settings

**Interfaces:**
- Produces: `UploadValidationError`, `UploadTooLarge`, `InvalidOOXML`
- Produces: `validate_upload_size(field_file) -> None`
- Produces: `validate_ooxml(field_file, expected_kind: str) -> None`
- Produces: `docx_bytes(*, macro_markers=()) -> bytes`, `xlsx_bytes(*, macro_markers=()) -> bytes` test helpers

- [ ] **Step 1: Write RED tests for the common size contract**

Add to `apps/core/tests/test_upload_validation.py`:

```python
import io
from unittest import mock

from django.core.files.base import File
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from apps.core import upload_validation


class UploadSizeTests(SimpleTestCase):
    @override_settings(UPLOAD_MAX_BYTES=10)
    def test_exact_limit_passes(self):
        upload = SimpleUploadedFile("x.docx", b"x" * 10)
        upload_validation.validate_upload_size(upload)

    @override_settings(UPLOAD_MAX_BYTES=10)
    def test_one_byte_over_limit_rejects(self):
        upload = SimpleUploadedFile("x.docx", b"x" * 11)
        with self.assertRaises(upload_validation.UploadTooLarge):
            upload_validation.validate_upload_size(upload)

    @override_settings(UPLOAD_MAX_BYTES=10)
    def test_seek_fallback_restores_position(self):
        stream = io.BytesIO(b"12345")
        field = File(stream, name="x.docx")
        field.size = None
        upload_validation.validate_upload_size(field)
        self.assertEqual(stream.tell(), 0)

    @override_settings(UPLOAD_MAX_BYTES=10)
    def test_unseekable_unknown_size_rejects_fail_closed(self):
        field = mock.Mock()
        field.size = None
        field.file.seek.side_effect = OSError("not seekable")
        with self.assertRaises(upload_validation.UploadValidationError):
            upload_validation.validate_upload_size(field)
```

- [ ] **Step 2: Run the size tests and confirm RED**

Run:

```bash
python manage.py test apps.core.tests.test_upload_validation.UploadSizeTests
```

Expected: import/module failure because `apps.core.upload_validation` does not yet exist.

- [ ] **Step 3: Add settings for a single shared byte limit**

In `config/settings/base.py` replace the independent OCR byte constant with:

```python
UPLOAD_MAX_BYTES = int(os.environ.get("UPLOAD_MAX_BYTES", str(150 * 1024 * 1024)))
OCR_MAX_BYTES = UPLOAD_MAX_BYTES
```

In `.env.example` add:

```dotenv
# Unified maximum size for all accepted uploads and OCR originals: 150 MiB by default.
UPLOAD_MAX_BYTES=157286400
```

Do not add a separate OCR byte environment knob.

- [ ] **Step 4: Implement the minimal size validator and exception hierarchy**

Start `apps/core/upload_validation.py` with:

```python
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
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

- [ ] **Step 5: Re-run size tests and confirm GREEN**

Run:

```bash
python manage.py test apps.core.tests.test_upload_validation.UploadSizeTests
```

Expected: PASS.

- [ ] **Step 6: Add valid minimal OOXML builders for tests**

Create `apps/core/tests/upload_fixtures.py` with builders that include valid package declarations:

```python
import io
import zipfile

CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _ooxml_bytes(main_part: str, main_type: str, extra=()) -> bytes:
    buf = io.BytesIO()
    content_types = (
        f'<Types xmlns="{CONTENT_TYPES_NS}">'
        f'<Override PartName="/{main_part}" ContentType="{main_type}"/>'
        "</Types>"
    )
    rels = f'<Relationships xmlns="{RELS_NS}"/>'
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr(main_part, "<root/>")
        for name, payload in extra:
            zf.writestr(name, payload)
    return buf.getvalue()


def docx_bytes(*, extra=()) -> bytes:
    return _ooxml_bytes(
        "word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        extra,
    )


def xlsx_bytes(*, extra=()) -> bytes:
    return _ooxml_bytes(
        "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        extra,
    )
```

- [ ] **Step 7: Write RED OOXML tests**

Cover all contract edges in `apps/core/tests/test_upload_validation.py`:

```python
class OOXMLValidationTests(SimpleTestCase):
    def test_arbitrary_bytes_renamed_docx_reject(self): ...
    def test_malformed_zip_xlsx_rejects(self): ...
    def test_docx_requires_document_xml(self): ...
    def test_xlsx_requires_workbook_xml(self): ...
    def test_docx_rejects_xlsx_container(self): ...
    def test_xlsx_rejects_docx_container(self): ...
    def test_mismatched_content_type_rejects(self): ...
    def test_duplicate_main_part_rejects(self): ...
    def test_valid_docx_passes(self): ...
    def test_valid_xlsx_passes(self): ...
    def test_stream_position_is_restored_on_success(self): ...
    def test_stream_position_is_restored_on_failure(self): ...
```

Use `SimpleUploadedFile("x.docx", docx_bytes())` and `xlsx_bytes()` for valid inputs. Create malformed/duplicate cases directly with `zipfile.ZipFile` so the tests prove each rule independently.

- [ ] **Step 8: Run OOXML tests and confirm RED**

Run:

```bash
python manage.py test apps.core.tests.test_upload_validation.OOXMLValidationTests
```

Expected: FAIL because `validate_ooxml` is absent.

- [ ] **Step 9: Implement OOXML validation minimally**

Use these fixed contracts:

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
    try:
        main_part, expected_type = OOXML_CONTRACTS[expected_kind]
    except KeyError as exc:
        raise ValueError(f"unsupported OOXML kind: {expected_kind}") from exc

    source = _source_file(field_file)
    source.seek(0)
    try:
        with zipfile.ZipFile(source) as zf:
            names = zf.namelist()
            for required in ("[Content_Types].xml", "_rels/.rels", main_part):
                if names.count(required) != 1:
                    raise InvalidOOXML("Некорректная структура файла DOCX/XLSX.")
            try:
                content_types = ElementTree.fromstring(zf.read("[Content_Types].xml"))
                ElementTree.fromstring(zf.read("_rels/.rels"))
                zf.read(main_part)
            except (ElementTree.ParseError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
                raise InvalidOOXML("Некорректная структура файла DOCX/XLSX.") from exc

            override = {
                node.attrib.get("PartName"): node.attrib.get("ContentType")
                for node in content_types.findall(f"{{{CONTENT_TYPES_NS}}}Override")
            }
            if override.get(f"/{main_part}") != expected_type:
                raise InvalidOOXML("Файл не соответствует заявленному формату DOCX/XLSX.")
    except (zipfile.BadZipFile, OSError) as exc:
        raise InvalidOOXML("Некорректная структура файла DOCX/XLSX.") from exc
    finally:
        source.seek(0)
```

Keep this structural only; do not move macro-marker logic out of `macro_check.py`.

- [ ] **Step 10: Run Task 1 tests and commit**

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

### Task 2: Add shared temporary-file handling and ordinary PDF validation

**Files:**
- Modify: `apps/core/upload_validation.py`
- Modify: `apps/core/tests/test_upload_validation.py`

**Interfaces:**
- Consumes: `UploadValidationError`, `InvalidPDF`, `FormatValidatorUnavailable`, `validate_upload_size`
- Produces: `_temporary_upload_path(field_file)` private context manager
- Produces: `validate_pdf(field_file) -> None`

- [ ] **Step 1: Write RED tests for temp-file lifecycle**

Patch `tempfile.NamedTemporaryFile` only enough to record the path; assert:

```python
class TemporaryUploadTests(SimpleTestCase):
    def test_source_position_restored_after_copy(self): ...
    def test_temp_file_removed_after_success(self): ...
    def test_temp_file_removed_when_consumer_raises(self): ...
```

The helper must copy in bounded chunks; tests should use a custom source whose `read()` records requested chunk size and assert no unbounded `read()` is used.

- [ ] **Step 2: Implement `_temporary_upload_path`**

Implement as a context manager with `NamedTemporaryFile(delete=False)` and `shutil.copyfileobj(source, tmp, length=1024 * 1024)`. Always restore `source.seek(0)` and always unlink the temp path in `finally`.

- [ ] **Step 3: Write RED Poppler tests**

Use `mock.patch("apps.core.upload_validation.pdfinfo_from_path")`:

```python
class PDFValidationTests(SimpleTestCase):
    def test_valid_pdf_with_one_page_passes(self): ...
    def test_zero_page_pdf_rejects(self): ...
    def test_poppler_parse_error_rejects_as_invalid_pdf(self): ...
    def test_missing_poppler_rejects_fail_closed(self): ...
    def test_poppler_timeout_rejects_fail_closed(self): ...
    def test_stream_position_restored(self): ...
```

Import `PDFInfoNotInstalledError`, `PDFPageCountError`, and `PDFPopplerTimeoutError` from `pdf2image.exceptions` in the test so mappings are explicit.

- [ ] **Step 4: Run PDF tests and confirm RED**

Run:

```bash
python manage.py test apps.core.tests.test_upload_validation.PDFValidationTests
```

Expected: FAIL because `validate_pdf` is absent.

- [ ] **Step 5: Implement ordinary PDF validation through Poppler**

Add:

```python
from pdf2image import pdfinfo_from_path
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError, PDFPopplerTimeoutError


def validate_pdf(field_file) -> None:
    validate_upload_size(field_file)
    try:
        with _temporary_upload_path(field_file) as path:
            info = pdfinfo_from_path(path, timeout=settings.OCR_PROCESS_TIMEOUT)
    except PDFPageCountError as exc:
        raise InvalidPDF("Некорректный PDF-файл.") from exc
    except (PDFInfoNotInstalledError, PDFPopplerTimeoutError, OSError) as exc:
        logger.exception("PDF validator unavailable")
        raise FormatValidatorUnavailable(
            "Проверка формата временно недоступна; файл не сохранён."
        ) from exc
    if int(info.get("Pages", 0)) < 1:
        raise InvalidPDF("Некорректный PDF-файл.")
```

Malformed user content maps to `InvalidPDF`; missing executable/timeouts map to infrastructure failure.

- [ ] **Step 6: Run Task 2 tests and commit**

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

### Task 3: Add the fail-closed PDF/A-2b veraPDF wrapper

**Files:**
- Modify: `apps/core/upload_validation.py`
- Modify: `apps/core/tests/test_upload_validation.py`
- Modify: `config/settings/base.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `validate_upload_size`, `validate_pdf`, `_temporary_upload_path`
- Produces: `validate_pdfa_2b(field_file) -> None`
- Produces: `PDFAValidationFailed`, `FormatValidatorUnavailable`

- [ ] **Step 1: Add runtime settings**

In `config/settings/base.py`:

```python
VERAPDF_EXECUTABLE = os.environ.get("VERAPDF_EXECUTABLE", "verapdf")
VERAPDF_TIMEOUT_SECONDS = int(os.environ.get("VERAPDF_TIMEOUT_SECONDS", "60"))
```

In `.env.example`:

```dotenv
# Local veraPDF Greenfield CLI used for PDF/A-2b intake validation.
VERAPDF_EXECUTABLE=verapdf
VERAPDF_TIMEOUT_SECONDS=60
```

- [ ] **Step 2: Write RED wrapper tests**

Patch `subprocess.run` and `validate_pdf` so each exit branch is isolated:

```python
class PDFAValidationTests(SimpleTestCase):
    def test_structural_pdf_validation_runs_before_verapdf(self): ...
    def test_exit_zero_passes(self): ...
    def test_exit_one_is_user_nonconformance(self): ...
    def test_exit_two_is_infrastructure_failure(self): ...
    def test_missing_executable_is_infrastructure_failure(self): ...
    def test_timeout_is_infrastructure_failure(self): ...
    def test_shell_is_never_used(self): ...
    def test_fixed_flavour_is_2b(self): ...
    def test_diagnostics_are_not_exposed_in_exception_message(self): ...
```

For the command assertion require this argument vector shape:

```python
[
    settings.VERAPDF_EXECUTABLE,
    "--format", "text",
    "--maxfailures", "1",
    "--maxfailuresdisplayed", "1",
    "--loglevel", "1",
    "-f", "2b",
    temp_path,
]
```

- [ ] **Step 3: Run wrapper tests and confirm RED**

Run:

```bash
python manage.py test apps.core.tests.test_upload_validation.PDFAValidationTests
```

Expected: FAIL because `validate_pdfa_2b` is absent.

- [ ] **Step 4: Implement the wrapper minimally**

Implement `validate_pdfa_2b()` so it first calls `validate_pdf(field_file)`, then creates one fresh temp file for veraPDF and runs:

```python
completed = subprocess.run(
    command,
    check=False,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    timeout=settings.VERAPDF_TIMEOUT_SECONDS,
    shell=False,
)
```

Map results exactly:

```python
if completed.returncode == 0:
    return
if completed.returncode == 1:
    raise PDFAValidationFailed("Файл не соответствует профилю PDF/A-2b.")
logger.error(
    "veraPDF runtime failure rc=%s diagnostic=%r",
    completed.returncode,
    (completed.stderr or completed.stdout or "")[:2000],
)
raise FormatValidatorUnavailable(
    "Проверка формата временно недоступна; файл не сохранён."
)
```

Catch `FileNotFoundError`, `subprocess.TimeoutExpired`, and `OSError` and map them to the same safe infrastructure exception. Never include temp paths or raw subprocess output in the user-facing exception.

- [ ] **Step 5: Run Task 3 tests and commit**

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

### Task 4: Integrate validation into `NormativeDocument` without storage side effects

**Files:**
- Modify: `apps/documents/models.py` in `NormativeDocument.save()`
- Create: `apps/documents/tests/test_upload_format_validation.py`
- Modify: `apps/documents/tests/test_antivirus_integration.py`
- Modify: `apps/documents/tests/test_macro_check_integration.py`

**Interfaces:**
- Consumes: `upload_validation.validate_pdfa_2b`, `validate_ooxml`, `validate_upload_size`
- Preserves: `antivirus.needs_scan`, `scan_uploaded_field`, `macro_check.reject_if_has_macros`

- [ ] **Step 1: Write RED model-boundary tests**

Create tests that patch storage/staging boundaries rather than merely asserting no DB row:

```python
class NormativeDocumentUploadFormatTests(TestCase):
    def test_original_non_pdfa_is_rejected_before_antivirus_and_storage(self): ...
    def test_arbitrary_docx_bytes_rejected_before_antivirus_and_storage(self): ...
    def test_valid_original_calls_pdfa_then_antivirus(self): ...
    def test_valid_editable_calls_ooxml_then_antivirus_then_macro_check(self): ...
    def test_resave_without_new_upload_does_not_revalidate(self): ...
```

For reject tests patch:

```python
mock.patch("apps.core.antivirus.scan_uploaded_field")
mock.patch.object(NormativeDocument._meta.get_field("files_original").storage, "save")
```

and where relevant `apps.core.staged_files.stage_uploaded_file`. Assert all remain uncalled after a format failure.

- [ ] **Step 2: Run document regressions and confirm RED**

Run:

```bash
python manage.py test apps.documents.tests.test_upload_format_validation
```

Expected: FAIL because model save does not yet invoke content validation.

- [ ] **Step 3: Integrate validation before antivirus**

Change imports to:

```python
from apps.core import antivirus, macro_check, upload_validation
```

Inside the existing `if antivirus.needs_scan(field_file):` block:

```python
upload_validation.validate_upload_size(field_file)
if field_name == "files_original":
    upload_validation.validate_pdfa_2b(field_file)
else:
    expected_kind = Path(field_file.name).suffix.lower().lstrip(".")
    upload_validation.validate_ooxml(field_file, expected_kind)

antivirus.scan_uploaded_field(...)
if field_name == "files_editable":
    macro_check.reject_if_has_macros(...)
```

Import `Path` from `pathlib`. Do not run `macro_check` for the PDF original.

- [ ] **Step 4: Repair existing antivirus/macro tests so they still reach their intended layer**

Current tests use fake bytes such as `b"%PDF-1.4 clean content"` and malformed ZIPs. Replace those test inputs with fixtures that pass the new structural gate:

- use the checked-in `pdfa-2b-valid.pdf` for clean `files_original`;
- for EICAR-in-PDF tests, create/use a structurally valid PDF fixture containing the EICAR marker only if the live clamd fixture proves detection; otherwise patch `validate_pdfa_2b` in antivirus-focused tests so the test remains about ClamAV, not format validation;
- update generated DOCX helpers to include `[Content_Types].xml`, `_rels/.rels`, valid main-part MIME and `word/document.xml` before adding macro markers.

The preferred test isolation rule is: format tests use the real format validator; antivirus tests may patch the format validator; macro tests use structurally valid OOXML and real macro detection.

- [ ] **Step 5: Run focused document tests and commit**

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
git commit -m "feat: enforce upload format validation on documents"
```

---

### Task 5: Integrate validation into published template uploads

**Files:**
- Modify: `apps/templates_bank/models.py` in `Template.save()`
- Create: `apps/templates_bank/test_upload_format_validation.py`
- Modify: `apps/templates_bank/tests.py`

**Interfaces:**
- Consumes: `validate_upload_size`, `validate_ooxml`, `validate_pdf`
- Preserves: existing template WORM staging, ClamAV, macro audit and status audit behavior

- [ ] **Step 1: Write RED template boundary tests**

Cover all four relevant outcomes:

```python
class TemplateUploadFormatTests(TestCase):
    def test_editable_arbitrary_bytes_rejected_before_antivirus_and_storage(self): ...
    def test_editable_type_mismatch_rejected(self): ...
    def test_sample_malformed_pdf_rejected_before_antivirus_and_storage(self): ...
    def test_sample_valid_ordinary_non_pdfa_is_accepted(self): ...
    def test_valid_editable_runs_ooxml_then_antivirus_then_macro(self): ...
    def test_existing_committed_file_names_are_not_revalidated(self): ...
```

`test_sample_valid_ordinary_non_pdfa_is_accepted` is important: it proves the design did not silently turn `Template.file_sample` into PDF/A.

- [ ] **Step 2: Run template tests and confirm RED**

Run:

```bash
python manage.py test apps.templates_bank.test_upload_format_validation
```

Expected: FAIL because `Template.save()` does not yet invoke content validation.

- [ ] **Step 3: Integrate per-field validation before ClamAV**

Import `Path` and `upload_validation`, then inside the existing new-upload guard:

```python
upload_validation.validate_upload_size(field_file)
if field_name == "file_editable":
    expected_kind = Path(field_file.name).suffix.lower().lstrip(".")
    upload_validation.validate_ooxml(field_file, expected_kind)
else:
    upload_validation.validate_pdf(field_file)

antivirus.scan_uploaded_field(...)
if field_name == "file_editable":
    macro_check.reject_if_has_macros(...)
```

Do not invoke macro detection for `file_sample` PDF.

- [ ] **Step 4: Update existing template antivirus/macro test fixtures**

Change `_docx_bytes()` in `apps/templates_bank/tests.py` to use `apps.core.tests.upload_fixtures.docx_bytes()` and add the macro member via its `extra` argument. Antivirus-focused tests should patch only the new format layer when they intentionally feed EICAR bytes that are not structurally valid documents.

- [ ] **Step 5: Run focused template tests and commit**

Run:

```bash
python manage.py test apps.templates_bank.test_upload_format_validation apps.templates_bank.tests
```

Expected: PASS.

Commit:

```bash
git add apps/templates_bank/models.py apps/templates_bank/test_upload_format_validation.py apps/templates_bank/tests.py
git commit -m "feat: enforce upload format validation on templates"
```

---

### Task 6: Pin veraPDF 1.30.2, vendor known fixtures, and add real runtime smoke checks

**Files:**
- Create: `apps/core/tests/fixtures/pdfa-2b-valid.pdf`
- Create: `apps/core/tests/fixtures/pdfa-2b-invalid.pdf`
- Create: `apps/core/tests/fixtures/README.md`
- Create: `deploy/verapdf/auto-install.xml`
- Create: `deploy/verapdf/install.sh`
- Create: `deploy/verapdf/validate.sh`
- Create: `deploy/verapdf/SHA256SUMS`
- Create: `deploy/verapdf/README.md`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: `/opt/verapdf/verapdf` by default on CI/production nodes
- Consumes: official installer `verapdf-greenfield-1.30.2-installer.zip`
- Consumes: fixed GPG signing-key fingerprint `13DD 102B 4DD6 9354 D12D E5A8 3184 8632 78B1 7FE7`
- Produces: `deploy/verapdf/install.sh [install-root]`
- Produces: `deploy/verapdf/validate.sh [verapdf-executable]`

- [ ] **Step 1: Vendor two small corpus fixtures with exact provenance**

Use the veraPDF corpus `master` files:

```text
PASS:
PDF_A-2b/6.1 File structure/6.1.2 File header/veraPDF test suite 6-1-2-t02-pass-a.pdf
Git blob: 1bad81119b6bbf2cb52d2713f57955bf32bbe749

FAIL:
PDF_A-2b/6.1 File structure/6.1.2 File header/veraPDF test suite 6-1-2-t02-fail-a.pdf
Git blob: f01744ef9a17e5a6442a5de7644f6d251e7cf879
```

Store them as `apps/core/tests/fixtures/pdfa-2b-valid.pdf` and `pdfa-2b-invalid.pdf`. In `README.md` record source repository `veraPDF/veraPDF-corpus`, original paths, blob SHAs, retrieval date `2026-09-12`, and CC BY 4.0 corpus license.

- [ ] **Step 2: Verify the upstream installer signature once, then pin its SHA-256**

Use the official release pair:

```text
https://software.verapdf.org/rel/1.30/verapdf-greenfield-1.30.2-installer.zip
https://software.verapdf.org/rel/1.30/verapdf-greenfield-1.30.2-installer.zip.asc
```

Before writing the repository checksum, verify the detached signature against the official fingerprint:

```bash
gpg --fingerprint 78B17FE7
gpg --verify verapdf-greenfield-1.30.2-installer.zip.asc verapdf-greenfield-1.30.2-installer.zip
sha256sum verapdf-greenfield-1.30.2-installer.zip > deploy/verapdf/SHA256SUMS
```

Reject the pin if the displayed fingerprint is not exactly:

```text
13DD 102B 4DD6 9354 D12D E5A8 3184 8632 78B1 7FE7
```

`deploy/verapdf/SHA256SUMS` must contain the resulting 64-hex digest and exact filename; do not commit a placeholder digest. Ordinary deployments verify only this committed SHA-256 and do not depend on a keyserver.

- [ ] **Step 3: Add a deterministic non-interactive installer descriptor**

`deploy/verapdf/auto-install.xml` must select the Unix CLI/script pack and install to a path supplied by the installer invocation. Base the pack set on upstream IzPack metadata; the outcome requirement is `/opt/verapdf/verapdf` executable and `verapdf --version` reporting 1.30.2.

- [ ] **Step 4: Add `deploy/verapdf/install.sh` with checksum enforcement**

Script requirements:

```bash
#!/usr/bin/env bash
set -euo pipefail

VERSION="1.30.2"
ARCHIVE="verapdf-greenfield-${VERSION}-installer.zip"
BASE_URL="https://software.verapdf.org/rel/1.30"
INSTALL_ROOT="${1:-/opt/verapdf}"
```

The script must:

1. create a temporary working directory with `mktemp -d` and trap cleanup;
2. download exactly `$BASE_URL/$ARCHIVE`;
3. verify it with `sha256sum -c` using the committed digest from `deploy/verapdf/SHA256SUMS`;
4. unzip the archive;
5. invoke the contained `verapdf-izpack-installer-1.30.2.jar` with `deploy/verapdf/auto-install.xml`;
6. fail if `$INSTALL_ROOT/verapdf` is absent/non-executable;
7. never fall back to an unpinned “latest” URL.

- [ ] **Step 5: Write `deploy/verapdf/validate.sh` before wiring CI**

The script must fail unless all commands succeed:

```bash
"$VERAPDF" --version | grep -F "1.30.2"
"$VERAPDF" -l | grep -F "2b"
"$VERAPDF" -f 2b apps/core/tests/fixtures/pdfa-2b-valid.pdf
```

For the known non-conforming fixture, explicitly require exit code 1:

```bash
set +e
"$VERAPDF" -f 2b apps/core/tests/fixtures/pdfa-2b-invalid.pdf
rc=$?
set -e
[[ "$rc" -eq 1 ]]
```

Also run `pdfinfo apps/core/tests/fixtures/pdfa-2b-valid.pdf >/dev/null` and the same for the invalid PDF to prove both files are structurally parseable PDFs.

- [ ] **Step 6: Test the deployment scripts locally/CI-like**

Run:

```bash
bash -n deploy/verapdf/install.sh
bash -n deploy/verapdf/validate.sh
sudo apt-get install -y default-jre-headless unzip
sudo bash deploy/verapdf/install.sh /opt/verapdf
bash deploy/verapdf/validate.sh /opt/verapdf/verapdf
```

Expected: all commands exit 0; the invalid fixture is observed specifically as veraPDF exit 1 inside the validator script.

- [ ] **Step 7: Wire veraPDF installation before Django tests on both CI matrix legs**

In `.github/workflows/ci.yml`, after OCR system packages and before Django checks, add Java/unzip and the pinned installer:

```yaml
- name: Установить veraPDF 1.30.2
  run: |
    sudo apt-get install -y -qq default-jre-headless unzip
    sudo bash deploy/verapdf/install.sh /opt/verapdf
    echo "/opt/verapdf" >> "$GITHUB_PATH"

- name: Проверить veraPDF/Poppler intake validators
  run: |
    bash -n deploy/verapdf/validate.sh
    bash deploy/verapdf/validate.sh /opt/verapdf/verapdf
```

Because the workflow job is matrixed, this smoke runs independently on PostgreSQL 16 and 18.

- [ ] **Step 8: Add one real-runtime Django smoke test**

In `apps/core/tests/test_upload_validation.py`, add a test class skipped only when `VERAPDF_EXECUTABLE` cannot be resolved outside CI, but not skipped in CI where the executable is installed:

```python
class RealValidatorSmokeTests(SimpleTestCase):
    def test_checked_in_pdfa_fixture_passes_real_verapdf(self): ...
    def test_checked_in_non_pdfa_fixture_is_rejected(self): ...
    def test_checked_in_pdf_fixture_passes_real_poppler(self): ...
```

Do not mock `subprocess.run` or `pdfinfo_from_path` in this class.

- [ ] **Step 9: Run focused runtime tests and commit**

Run:

```bash
bash deploy/verapdf/validate.sh /opt/verapdf/verapdf
VERAPDF_EXECUTABLE=/opt/verapdf/verapdf python manage.py test apps.core.tests.test_upload_validation.RealValidatorSmokeTests
```

Expected: PASS.

Commit:

```bash
git add apps/core/tests/fixtures apps/core/tests/test_upload_validation.py deploy/verapdf .github/workflows/ci.yml
git commit -m "build: pin veraPDF and validate PDF-A runtime"
```

---

### Task 7: Verify ordering, storage invariants, documentation, and the full regression suite

**Files:**
- Modify: `README.md`
- Modify: `STACK.md`
- Review only: all files changed in Tasks 1–6

**Interfaces:**
- Consumes all validators and model integrations from previous tasks
- Produces no new runtime API

- [ ] **Step 1: Add explicit ordering regressions**

Add tests that record events from patched functions and assert exact order:

```python
# Document editable
aevents == ["size", "ooxml", "antivirus", "macro", "save"]

# Document original
events == ["size", "pdfa", "antivirus", "save"]

# Template sample
events == ["size", "pdf", "antivirus", "save"]
```

The test may patch the storage/model save boundary to append `"save"`; it must not rely only on mock call counts.

- [ ] **Step 2: Add explicit no-storage/no-staging regressions for every rejection class**

For each field (`files_original`, `files_editable`, `file_editable`, `file_sample`) cover at least one validation failure and assert:

- storage `.save()` not called;
- staged promotion helper not called for WORM-bound fields;
- DB row not created for new objects;
- ClamAV not called when format validation fails.

- [ ] **Step 3: Verify OCR shares the intake byte setting**

Add a settings regression in `apps/core/tests/test_production_settings.py` or the most appropriate existing settings test:

```python
from django.conf import settings
self.assertEqual(settings.OCR_MAX_BYTES, settings.UPLOAD_MAX_BYTES)
self.assertEqual(settings.UPLOAD_MAX_BYTES, 150 * 1024 * 1024)
```

Where environment overrides are under test, verify overriding `UPLOAD_MAX_BYTES` changes both settings together.

- [ ] **Step 4: Update operational documentation**

`README.md` must state:

- all upload types have a 150 MiB intake cap;
- original NРД must be PDF/A-2b;
- editable files must be genuine DOCX/XLSX OOXML without macros/ActiveX;
- template sample PDFs are ordinary valid PDFs;
- Poppler and veraPDF failures reject uploads fail-closed.

`STACK.md` must record:

- veraPDF Greenfield 1.30.2 local CLI dependency;
- fixed PDF/A-2b policy;
- installer checksum pin and upgrade procedure in `deploy/verapdf/README.md`;
- validation ordering before ClamAV/storage.

- [ ] **Step 5: Run all focused tests**

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

Expected: PASS with zero failures/errors.

- [ ] **Step 6: Run static/configuration checks**

Run:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
bash -n deploy/verapdf/install.sh
bash -n deploy/verapdf/validate.sh
bash deploy/verapdf/validate.sh /opt/verapdf/verapdf
```

Expected: all exit 0 and no migration is generated.

- [ ] **Step 7: Run the exact full project verification used by CI**

Run against the CI-equivalent PostgreSQL/Redis environment:

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

Expected: full suite passes, both coverage gates pass, Redis/worker integration passes.

- [ ] **Step 8: Self-review against every acceptance criterion**

Check each item from spec section 14 explicitly. In particular verify:

1. no format failure can reach storage/staging;
2. a valid ordinary non-PDF/A PDF still works for `Template.file_sample`;
3. a valid ordinary non-PDF/A PDF is rejected for `NormativeDocument.files_original`;
4. unavailable Poppler/veraPDF does not degrade to allow;
5. real veraPDF smoke runs on both CI matrix legs;
6. no new migration/audit event appeared.

Fix any discrepancy before the final commit.

- [ ] **Step 9: Commit docs/final regressions**

```bash
git add README.md STACK.md apps/core/tests apps/documents/tests apps/templates_bank
git commit -m "test: lock pre-WORM upload validation contract"
```

- [ ] **Step 10: Open the implementation PR as draft and use RED/GREEN evidence in its body**

The implementation PR body must include:

- the design/spec path;
- this implementation-plan path;
- RED evidence per task before production changes;
- GREEN focused-test evidence;
- final `Quality` + matrix `CI` evidence;
- real veraPDF 1.30.2 smoke evidence;
- statement that no schema migration was introduced.

Do not mark ready or merge until fresh GitHub `Quality` and both PostgreSQL 16/18 `CI` jobs complete successfully and review threads are empty/resolved.
