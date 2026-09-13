import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from apps.core import upload_validation
from apps.core.tests.upload_fixtures import XLSX_MAIN, XLSX_TYPE, package_bytes


class OOXMLResourceLimitTests(SimpleTestCase):
    @override_settings(OOXML_MAX_UNCOMPRESSED_BYTES=1024)
    def test_total_uncompressed_size_is_bounded_before_member_reads(self):
        payload = package_bytes(
            XLSX_MAIN,
            XLSX_TYPE,
            extra=(("xl/media/oversized.bin", b"A" * 2048),),
        )
        upload = SimpleUploadedFile("book.xlsx", payload)

        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(upload, "xlsx")

    @override_settings(
        OOXML_MAX_COMPRESSION_RATIO=5,
        OOXML_COMPRESSION_RATIO_MIN_BYTES=1024,
    )
    def test_high_compression_ratio_is_rejected(self):
        buf = io.BytesIO()
        content_types = (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            f'<Override PartName="/{XLSX_MAIN}" ContentType="{XLSX_TYPE}"/>'
            "</Types>"
        )
        rels = (
            '<Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
        )
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", rels)
            archive.writestr(XLSX_MAIN, "<root/>")
            archive.writestr("xl/media/bomb.bin", b"A" * 100_000)
        upload = SimpleUploadedFile("book.xlsx", buf.getvalue())

        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(upload, "xlsx")

    @override_settings(OOXML_MAX_ENTRIES=3)
    def test_archive_entry_count_is_bounded(self):
        payload = package_bytes(
            XLSX_MAIN,
            XLSX_TYPE,
            extra=(("xl/media/one.bin", b"1"),),
        )
        upload = SimpleUploadedFile("book.xlsx", payload)

        with self.assertRaises(upload_validation.InvalidOOXML):
            upload_validation.validate_ooxml(upload, "xlsx")
