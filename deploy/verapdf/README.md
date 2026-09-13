# veraPDF runtime

The application validates `NormativeDocument.files_original` as PDF/A-2b before any mutable staging or WORM promotion. Production and CI use veraPDF Greenfield **1.30.2** with the fixed `2b` flavour.

## Install

Prerequisites: Java runtime, `curl`, `unzip`, `sha256sum`, and Poppler (`pdfinfo`). Then run:

```bash
sudo bash deploy/verapdf/install.sh
bash deploy/verapdf/validate.sh /opt/verapdf/verapdf
```

`install.sh` downloads only `verapdf-greenfield-1.30.2-installer.zip` from the pinned release directory and refuses to execute it unless the digest matches `deploy/verapdf/SHA256SUMS`. There is no `latest` fallback.

## Supply-chain verification

The committed 1.30.2 checksum was produced only after verifying the upstream detached signature with the official veraPDF key. Required fingerprint:

```text
13DD 102B 4DD6 9354 D12D E5A8 3184 8632 78B1 7FE7
```

The verified installer SHA-256 is recorded in `SHA256SUMS`. Runtime installation deliberately uses the committed checksum and does not contact a keyserver.

## Upgrade procedure

For any version update:

1. Choose an explicit production release; do not point scripts at a moving `latest` URL.
2. Download the installer and detached `.asc` signature from the official veraPDF release site.
3. Import the official signing key and verify its full fingerprint is exactly the expected veraPDF fingerprint above (or a separately reviewed replacement fingerprint).
4. Verify the detached signature, then compute the installer SHA-256 and update `SHA256SUMS`.
5. Update the pinned version in `install.sh`, `validate.sh`, CI, settings/docs as needed.
6. Re-run `deploy/verapdf/validate.sh` and the Django `RealValidatorSmokeTests` against the checked-in pass/fail corpus fixtures on every supported CI database leg.

A missing executable, installation mismatch, timeout, or unexpected validator exit is an intake failure: the application remains fail-closed and does not save the upload.
