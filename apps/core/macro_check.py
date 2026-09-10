"""Обнаружение макросов/ActiveX в OOXML-документах (DOCX/XLSX) — ClamAV
(apps/core/antivirus.py) детектирует ИЗВЕСТНЫЕ вредоносные макросы по
сигнатурам, но не сам факт наличия VBA-кода в файле: непомеченный (пока
не известный антивирусу) макрос беспрепятственно пройдёт мимо clamd.
Простая структурная проверка — «есть ли в архиве VBA-проект/ActiveX
вообще» — не заменяет антивирус (не ловит вредоносное содержимое вне
макросов), а дополняет его: ТЗ 4.7 требует контроль целостности загрузки,
а не только антивирусную проверку по сигнатурам.

OOXML-документ (.docx/.xlsx) — это ZIP-архив с известной структурой папок;
наличие `vbaProject.bin`/`vbaData.xml`/`activeX/` внутри однозначно
указывает на встроенный код."""
from __future__ import annotations

import zipfile

MACRO_MARKERS = frozenset({
    "word/vbaProject.bin",
    "xl/vbaProject.bin",
    "word/vbaData.xml",
    "xl/vbaData.xml",
})
MACRO_MARKER_PREFIXES = ("word/activeX/", "xl/activeX/")


class MacrosDetected(Exception):
    def __init__(self, markers: list[str]):
        self.markers = markers
        super().__init__(f"Обнаружены макросы/ActiveX: {', '.join(markers)}")


def contains_macros(file) -> list[str]:
    """file — файлоподобный объект (Django File/UploadedFile/FieldFile) с
    read()/seek(). Возвращает отсортированный список найденных маркеров
    (пустой список — макросов нет).

    ЧЕСТНАЯ ГРАНИЦА: файлы, не являющиеся ZIP-архивом (обычный PDF,
    legacy .doc/.xls — OLE-контейнеры, а не ZIP), дают zipfile.BadZipFile
    и трактуются как «макросов нет» — legacy-форматы не проверяются: ТЗ
    2.2 §4.2.1 задаёт files_editable именно как DOCX/XLSX (OOXML), для
    .doc/.xls нужна была бы отдельная проверка потоков VBA через
    OLE-контейнер (например, пакетом olefile) — не реализовано, эти
    форматы в проекте не ожидаются на этом поле."""
    file.seek(0)
    try:
        with zipfile.ZipFile(file) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return []
    finally:
        file.seek(0)

    found = set(names) & MACRO_MARKERS
    found |= {name for name in names if name.startswith(MACRO_MARKER_PREFIXES)}
    return sorted(found)


def reject_if_has_macros(field_file, *, object_type: str, object_id: str) -> None:
    """Оборачивает contains_macros() аудитом — тот же принцип, что
    apps.core.antivirus.scan_uploaded_field(): при обнаружении маркеров
    пишет UPLOAD_MACRO_REJECTED в WORM-журнал и бросает MacrosDetected —
    вызывающий save() обязан прерваться, не записывать файл."""
    markers = contains_macros(field_file.file)
    if not markers:
        return

    from apps.audit.models import AuditLog

    AuditLog.objects.create(
        event_type=AuditLog.EventType.UPLOAD_MACRO_REJECTED,
        object_type=object_type, object_id=object_id,
        details={"field": field_file.field.name, "markers": markers},
    )
    raise MacrosDetected(markers)
