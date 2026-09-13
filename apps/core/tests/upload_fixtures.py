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
