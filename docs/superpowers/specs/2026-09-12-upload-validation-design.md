# Design: pre-WORM validation of PDF/A and OOXML uploads

Date: 2026-09-12
Status: approved design, implementation pending
Issue: #56

## 1. Goal

Prevent invalid or oversized files from reaching mutable staging or immutable/Object-Locked storage. The application must validate the actual file structure and declared compliance before any storage write that can later be promoted into WORM.

This closes the gap where file extension validators, ClamAV and macro detection prove only parts of the upload contract. In particular, a renamed arbitrary file can currently pass the extension check, malformed OOXML can be treated as “no macros”, and an ordinary PDF can reach the original-file pipeline without proving PDF/A conformance.

The accepted contract is:

- maximum upload size: 150 MiB;
- `NormativeDocument.files_original`: PDF/A-2b;
- `NormativeDocument.files_editable`: valid DOCX or XLSX OOXML, matching the filename extension;
- `Template.file_editable`: valid DOCX or XLSX OOXML, matching the filename extension;
- `Template.file_sample`: valid ordinary PDF; PDF/A is not required by the current model contract;
- validator failures and validator infrastructure failures are fail-closed.

## 2. Non-goals

This change does not:

- introduce automatic PDF conversion or repair;
- accept legacy `.doc`/`.xls` OLE containers;
- loosen the existing macro/ActiveX prohibition;
- replace ClamAV;
- change Object Lock retention rules;
- make `Template.file_sample` PDF/A;
- move validation into an external HTTP service;
- add a new audit event family solely for format rejection.

Rejected uploads remain transient input failures. Existing malware/macro-specific WORM audit events keep their current semantics.

## 3. Architecture

Create `apps/core/upload_validation.py` as the single content-validation boundary for new file uploads. Models call this boundary before antivirus/macro checks and before any `FieldFile` storage side effect.

The module has four independent responsibilities:

1. enforce the 150 MiB limit;
2. validate OOXML container structure;
3. validate ordinary PDF structure where the field contract is only PDF;
4. validate PDF/A-2b conformance through a pinned local veraPDF CLI.

The module operates only on newly supplied file objects. Existing stored field names must not be reopened and revalidated on unrelated model saves; the existing `antivirus.needs_scan()` convention remains the guard for distinguishing a new upload from an already committed field value.

### 3.1 Public service surface

The module should expose narrow functions rather than one mode-heavy generic API:

- `validate_upload_size(field_file)`
- `validate_ooxml(field_file, expected_kind)` where `expected_kind` is `docx` or `xlsx`
- `validate_pdf(field_file)`
- `validate_pdfa_2b(field_file)`

A small coordinator may be added if it keeps model code declarative, for example `validate_uploaded_field(field_file, contract=...)`, but the format-specific functions remain directly unit-testable.

All functions must restore the input stream position to the beginning before returning or raising so that ClamAV, macro detection and the eventual save see the full original stream.

## 4. Validation order

For every newly uploaded field:

1. size check;
2. content/format validation;
3. ClamAV scan;
4. macro/ActiveX check for OOXML;
5. existing model save/staging logic.

Rationale:

- size rejection is cheapest and prevents expensive work on an upload that is invalid regardless of content;
- malformed/non-conforming files never need antivirus work because they will not be stored;
- ClamAV remains required for structurally valid accepted formats;
- macro detection remains an additional policy check after the OOXML container has been proven valid.

No storage write occurs before steps 1–4 succeed.

## 5. Size validation

`MAX_UPLOAD_BYTES = 150 * 1024 * 1024` is the common intake limit and should be exposed through Django settings as `UPLOAD_MAX_BYTES`, defaulting to the same value. `OCR_MAX_BYTES` should reuse that setting rather than defining an independent 150 MiB constant, preventing the intake and OCR limits from silently diverging.

For Django uploaded files, prefer `.size`. If size metadata is unavailable, determine size from a seekable stream using `seek/tell` and restore the original position. If size cannot be determined reliably, reject the upload rather than proceeding without a limit.

The validator must reject strictly greater than 150 MiB; exactly 150 MiB is accepted.

## 6. OOXML validation

A `.docx` or `.xlsx` upload must be a readable ZIP container and must contain the mandatory package parts for the declared type.

Common requirements:

- ZIP can be opened and central directory read;
- `[Content_Types].xml` exists;
- `_rels/.rels` exists;
- archive member names are normalized and duplicate mandatory members are rejected;
- encrypted/corrupt archives that Python cannot read are rejected.

DOCX requires:

- `word/document.xml`.

XLSX requires:

- `xl/workbook.xml`.

The filename extension selects the expected container type. A valid XLSX renamed to `.docx` is rejected, and vice versa.

The validator does not attempt full ECMA-376 schema validation. The purpose is to establish that the file is a real OOXML package of the expected family before the existing macro/ActiveX policy is applied.

`apps/core/macro_check.py` keeps its current responsibility: detect VBA/ActiveX markers. Its `BadZipFile -> []` behavior becomes safe because production model flows call OOXML validation first; tests should document that `contains_macros()` itself is not a format validator.

## 7. PDF validation

### 7.1 Ordinary PDF

`Template.file_sample` requires a real PDF but not PDF/A. Validate at least that:

- the stream begins with a PDF header after no arbitrary prefix;
- the document is parseable by the project’s existing PDF stack or a minimal parser suitable for structural validation;
- parse errors reject the upload.

The implementation should reuse an already installed PDF parser if one exists in project dependencies; it should not add a second heavy PDF library solely for this check when Poppler or an existing Python dependency can provide a reliable parse result.

### 7.2 PDF/A-2b

`NormativeDocument.files_original` must conform specifically to PDF/A-2b. The profile is fixed policy, not a user-configurable setting.

veraPDF invocation:

- production release pinned to veraPDF 1.30.2;
- use built-in flavour `2b` (`-f 2b` / `--flavour 2b`);
- invoke with an argument vector through `subprocess.run`, never through `shell=True`;
- copy the already size-checked upload into a secure temporary file in bounded chunks;
- delete the temporary file in `finally`;
- enforce a configurable timeout;
- capture stdout/stderr only up to a bounded diagnostic amount before surfacing an error.

Exit-code policy follows veraPDF’s CLI contract:

- `0`: conforming, accept;
- `1`: validation completed and the document is non-conforming, reject as user input;
- any other exit code: validator/runtime failure, reject fail-closed as infrastructure/configuration error.

The upstream veraPDF test suite documents distinct non-validation statuses, including bad parameters, runtime failures and parse errors; therefore the application must not collapse every non-zero status into “ordinary non-conforming PDF/A”.

The executable path and timeout are configurable:

- `VERAPDF_EXECUTABLE`, default `verapdf`;
- `VERAPDF_TIMEOUT_SECONDS`, with a conservative production default.

The PDF/A profile is not configurable and remains `2b` until a separate approved change modifies the compliance policy.

## 8. Runtime and deployment

veraPDF is a local runtime dependency, not a network service.

Deployment must pin 1.30.2 and verify the downloaded distribution before installation. The deployment artifact should record both the pinned version and an integrity check (published signature and/or repository-maintained SHA-256 expected value). Production nodes and CI must use the same pinned release line.

The implementation should add a deployment validation command/script that verifies:

- executable is present and executable;
- `verapdf --version` reports the pinned production release;
- `verapdf -l` contains profile `2b`;
- a known conforming PDF/A-2b fixture returns 0;
- a known non-conforming PDF fixture returns 1.

The application itself remains fail-closed even if provisioning checks are bypassed: missing executable, timeout, unexpected exit status or subprocess failure rejects the original upload and performs no staging/WORM write.

The current CI job installs OCR and ClamAV system packages before Django tests. veraPDF provisioning/smoke validation should be added to the same CI path so both PostgreSQL matrix legs exercise the real validator runtime.

## 9. Model integration

### 9.1 NormativeDocument

For a newly uploaded `files_original`:

1. size;
2. PDF/A-2b;
3. ClamAV;
4. existing macro check may be skipped for PDF because OOXML macro markers are not applicable;
5. existing staged-original flow.

For a newly uploaded `files_editable`:

1. size;
2. OOXML matching `.docx`/`.xlsx`;
3. ClamAV;
4. macro/ActiveX rejection;
5. existing mutable working-file save.

### 9.2 Template

For `file_editable`:

1. size;
2. OOXML matching `.docx`/`.xlsx`;
3. ClamAV;
4. macro/ActiveX rejection;
5. existing protected-template staging/promotion.

For `file_sample`:

1. size;
2. ordinary PDF structural validation;
3. ClamAV;
4. existing protected-template staging/promotion.

No validator is run when a save merely references an already committed storage object.

## 10. Error model and UX

Create explicit validation exceptions under `apps.core.upload_validation`, preferably subclasses of Django `ValidationError` or translated to `ValidationError` at the model/form boundary:

- oversized upload;
- invalid OOXML container/type;
- invalid PDF;
- PDF/A-2b non-conformance;
- PDF/A validator unavailable/timeout/runtime failure.

User-input failures should be phrased as correctable field errors, without exposing subprocess internals.

Infrastructure failures should produce a safe user-facing message such as “Проверка PDF/A временно недоступна; файл не сохранён” and log the technical cause server-side. They must never degrade to accepting the upload.

The validator must not leak temporary paths, raw veraPDF command output or stack traces into the Web GUI/API response.

## 11. Transaction and storage guarantees

The core invariant is:

> If intake validation fails, no new object exists in working staging, mutable file storage, or WORM originals for that upload.

Validation therefore runs before the code path that creates a staged promotion or causes `FileField.pre_save()` to write bytes.

Existing compensating cleanup remains necessary for failures that happen after validation, because database/storage operations can still fail independently. This design does not remove `_cleanup_failed_file_writes()` or staged-upload cleanup.

Temporary files created solely for veraPDF are local ephemeral validation artifacts and are deleted independently of database transaction outcome.

## 12. Tests and RED/GREEN strategy

Implementation follows TDD.

### 12.1 Unit tests for upload validation

RED cases:

- `size == limit` passes;
- `size == limit + 1` rejects without invoking format validators;
- arbitrary bytes renamed `.docx` reject;
- malformed ZIP renamed `.xlsx` rejects;
- valid XLSX renamed `.docx` rejects;
- valid DOCX renamed `.xlsx` rejects;
- DOCX missing `word/document.xml` rejects;
- XLSX missing `xl/workbook.xml` rejects;
- valid minimal DOCX/XLSX fixtures pass structural validation;
- ordinary malformed PDF rejects;
- veraPDF exit 1 maps to PDF/A non-conformance;
- missing veraPDF executable rejects fail-closed;
- timeout rejects fail-closed;
- unexpected exit status rejects fail-closed;
- stream position is restored after every validator path.

### 12.2 Integration tests at model/service boundaries

Prove that rejected uploads do not call the storage save/staging boundary for:

- `NormativeDocument.files_original`;
- `NormativeDocument.files_editable`;
- `Template.file_editable`;
- `Template.file_sample`.

Prove valid fixtures continue through the existing antivirus/macro/save flow.

A regression specifically covers arbitrary bytes named `.docx`, because this is the current `BadZipFile -> no macros` gap.

### 12.3 Real veraPDF CI smoke

Use small checked-in or deterministically generated fixtures:

- one known PDF/A-2b conforming sample;
- one valid ordinary PDF that does not conform to PDF/A-2b.

The smoke test invokes the real pinned executable and asserts exit 0/1 respectively. Mocked subprocess tests remain useful for timeout/error branches but do not replace the real runtime smoke.

## 13. Acceptance criteria

Issue #56 is complete only when all of the following are true:

1. no upload over 150 MiB reaches storage;
2. renamed arbitrary bytes and malformed ZIPs are rejected as OOXML;
3. DOCX/XLSX type mismatch is rejected;
4. `NormativeDocument.files_original` accepts only PDF/A-2b;
5. an ordinary valid PDF that is not PDF/A-2b is rejected for `files_original`;
6. `Template.file_sample` accepts valid ordinary PDF without requiring PDF/A;
7. unavailable, timed-out or broken veraPDF causes fail-closed rejection;
8. rejected uploads create no staging/WORM object;
9. ClamAV and macro checks still run for files that pass structural validation;
10. CI exercises the real pinned veraPDF runtime on PostgreSQL 16 and 18 matrix legs;
11. existing Django suite, coverage gates and live Redis/worker integration remain green;
12. deployment documentation/configuration records the pinned veraPDF version and validation policy.

## 14. Implementation boundaries

Expected touched areas:

- new `apps/core/upload_validation.py`;
- new unit tests under `apps/core`;
- `apps/documents/models.py` and document regression tests;
- `apps/templates_bank/models.py` and template regression tests;
- `config/settings/base.py` / `.env.example`;
- CI provisioning/smoke validation;
- deployment/install validation for veraPDF;
- README/STACK operational documentation as needed.

No database schema change is required by the design itself. If implementation reveals a need for a schema or audit-event change, that is a scope change and must be reviewed separately rather than folded into #56 silently.

## 15. External validator facts fixed by this design

As verified on 2026-09-12:

- veraPDF production downloads provide Greenfield 1.30.2;
- upstream documentation lists built-in PDF/A-2b flavour `2b` and `-f/--flavour` selection;
- upstream `exit-status.sh` expects exit 0 for a conforming file and 1 for validation failure, while other classes of error use other statuses.

These facts justify the pinned CLI contract above; future veraPDF upgrades require re-running the smoke contract before changing the pin.
