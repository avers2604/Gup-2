"""apps/core/macro_check.py — детерминированные тесты на реальных
(сгенерированных на лету) ZIP/OOXML-структурах, без внешней инфраструктуры."""
import io
import zipfile

from django.test import SimpleTestCase

from apps.core.macro_check import MacrosDetected, contains_macros


def _zip_bytes(names: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, b"placeholder")
    return buf.getvalue()


class ContainsMacrosTests(SimpleTestCase):
    def test_plain_docx_without_macros_returns_empty(self):
        file = io.BytesIO(_zip_bytes(["[Content_Types].xml", "word/document.xml"]))
        self.assertEqual(contains_macros(file), [])

    def test_docx_with_vba_project_detected(self):
        file = io.BytesIO(_zip_bytes(["word/document.xml", "word/vbaProject.bin"]))
        self.assertEqual(contains_macros(file), ["word/vbaProject.bin"])

    def test_xlsx_with_vba_project_detected(self):
        file = io.BytesIO(_zip_bytes(["xl/workbook.xml", "xl/vbaProject.bin"]))
        self.assertEqual(contains_macros(file), ["xl/vbaProject.bin"])

    def test_vba_data_xml_detected(self):
        file = io.BytesIO(_zip_bytes(["word/document.xml", "word/vbaData.xml"]))
        self.assertEqual(contains_macros(file), ["word/vbaData.xml"])

    def test_activex_prefix_detected(self):
        file = io.BytesIO(_zip_bytes(["word/document.xml", "word/activeX/activeX1.xml"]))
        self.assertEqual(contains_macros(file), ["word/activeX/activeX1.xml"])

    def test_multiple_markers_all_reported_sorted(self):
        file = io.BytesIO(_zip_bytes(["word/vbaProject.bin", "word/activeX/activeX1.xml"]))
        self.assertEqual(
            contains_macros(file), ["word/activeX/activeX1.xml", "word/vbaProject.bin"],
        )

    def test_non_zip_file_returns_empty(self):
        # PDF/A (files_original) и legacy .doc/.xls — не ZIP, честная
        # граница: не проверяются (см. docstring contains_macros).
        file = io.BytesIO(b"%PDF-1.4 not a zip file at all")
        self.assertEqual(contains_macros(file), [])

    def test_file_position_restored_after_check(self):
        file = io.BytesIO(_zip_bytes(["word/document.xml"]))
        contains_macros(file)
        self.assertEqual(file.tell(), 0)

    def test_macros_detected_exception_lists_markers(self):
        exc = MacrosDetected(["word/vbaProject.bin"])
        self.assertIn("word/vbaProject.bin", str(exc))
        self.assertEqual(exc.markers, ["word/vbaProject.bin"])
