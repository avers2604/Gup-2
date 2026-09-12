import io
import zipfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from apps.core import upload_validation
from apps.core.tests.upload_fixtures import (
    CONTENT_TYPES_NS,
    DOCX_MAIN,
    DOCX_TYPE,
    RELS_NS,
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


def _rels() -> str:
    return f'<Relationships xmlns="{RELS_NS}"/>'


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
            ("_rels/.rels", _rels()),
        ])
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(
                SimpleUploadedFile("x.docx", payload), "docx"
            )

    def test_xlsx_missing_workbook_xml_rejects(self):
        payload = _custom_zip([
            ("[Content_Types].xml", _content_types(XLSX_MAIN, XLSX_TYPE)),
            ("_rels/.rels", _rels()),
        ])
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(
                SimpleUploadedFile("x.xlsx", payload), "xlsx"
            )

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
            ("_rels/.rels", _rels()),
            (DOCX_MAIN, "<document/>"),
        ])
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(
                SimpleUploadedFile("x.docx", payload), "docx"
            )

    def test_duplicate_main_part_rejects(self):
        payload = _custom_zip([
            ("[Content_Types].xml", _content_types(DOCX_MAIN, DOCX_TYPE)),
            ("_rels/.rels", _rels()),
            (DOCX_MAIN, "<document id='1'/>"),
            (DOCX_MAIN, "<document id='2'/>"),
        ])
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(
                SimpleUploadedFile("x.docx", payload), "docx"
            )

    def test_duplicate_content_types_rejects(self):
        payload = _custom_zip([
            ("[Content_Types].xml", _content_types(DOCX_MAIN, DOCX_TYPE)),
            ("[Content_Types].xml", _content_types(DOCX_MAIN, DOCX_TYPE)),
            ("_rels/.rels", _rels()),
            (DOCX_MAIN, "<document/>"),
        ])
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(
                SimpleUploadedFile("x.docx", payload), "docx"
            )

    def test_malformed_content_types_xml_rejects(self):
        payload = _custom_zip([
            ("[Content_Types].xml", "<Types>"),
            ("_rels/.rels", _rels()),
            (DOCX_MAIN, "<document/>"),
        ])
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(
                SimpleUploadedFile("x.docx", payload), "docx"
            )

    def test_malformed_relationships_xml_rejects(self):
        payload = _custom_zip([
            ("[Content_Types].xml", _content_types(DOCX_MAIN, DOCX_TYPE)),
            ("_rels/.rels", "<Relationships>"),
            (DOCX_MAIN, "<document/>"),
        ])
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(
                SimpleUploadedFile("x.docx", payload), "docx"
            )

    def test_entity_expansion_in_package_xml_rejects(self):
        dangerous_content_types = (
            '<!DOCTYPE Types [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            f'<Types xmlns="{CONTENT_TYPES_NS}">'
            f'<Override PartName="/{DOCX_MAIN}" ContentType="&xxe;"/>'
            "</Types>"
        )
        payload = _custom_zip([
            ("[Content_Types].xml", dangerous_content_types),
            ("_rels/.rels", _rels()),
            (DOCX_MAIN, "<document/>"),
        ])
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(
                SimpleUploadedFile("x.docx", payload), "docx"
            )

    def test_valid_docx_and_xlsx_pass(self):
        upload_validation.validate_ooxml(
            SimpleUploadedFile("x.docx", docx_bytes()), "docx"
        )
        upload_validation.validate_ooxml(
            SimpleUploadedFile("x.xlsx", xlsx_bytes()), "xlsx"
        )

    def test_unknown_kind_rejects_fail_closed(self):
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(
                SimpleUploadedFile("x.zip", docx_bytes()), "zip"
            )

    def test_stream_position_restored_after_success_and_failure(self):
        valid = SimpleUploadedFile("x.docx", docx_bytes())
        upload_validation.validate_ooxml(valid, "docx")
        self.assertEqual(valid.tell(), 0)

        invalid = SimpleUploadedFile("x.docx", b"bad")
        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(invalid, "docx")
        self.assertEqual(invalid.tell(), 0)
