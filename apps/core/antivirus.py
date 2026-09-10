"""Антивирусная проверка загружаемых файлов (ТЗ 4.7) — конвейер загрузки
НРД/бланков. ClamAV (clamd) поднят в docker-compose.yml с Этапа 1, но код
его не вызывал до этой партии.

Вызывается синхронно, ДО фактической записи файла в хранилище (см.
NormativeDocument.save()/Template.save()) — не после, как OCR: заражённый
файл не должен попасть в WORM-защищённый бакет originals, откуда его
потом может быть невозможно удалить."""
from __future__ import annotations

import clamd
from django.conf import settings


class MalwareDetected(Exception):
    """clamd нашёл сигнатуру в файле."""

    def __init__(self, signature: str):
        self.signature = signature
        super().__init__(f"Обнаружена сигнатура: {signature}")


class AntivirusUnavailable(Exception):
    """clamd недоступен, не ответил вовремя или ответил не по протоколу.

    Осознанно НЕ пропускаем файл молча при недоступности антивируса —
    непроверенный файл не должен оказаться в неизменяемом хранилище
    (см. docstring модуля). Вызывающий код (save() моделей) должен
    трактовать это исключение так же, как MalwareDetected: сохранение
    не проходит."""


def scan_file(file) -> None:
    """file — открытый файлоподобный объект (Django File/UploadedFile/
    FieldFile) с методами read()/seek(), ещё НЕ записанный в целевое
    хранилище. Ничего не возвращает — отсутствие исключения означает, что
    файл чист. Позиция файла восстанавливается после сканирования — тот
    же объект будет прочитан ещё раз при фактическом сохранении."""
    try:
        client = clamd.ClamdNetworkSocket(
            host=settings.CLAMAV_HOST, port=settings.CLAMAV_PORT, timeout=settings.CLAMAV_TIMEOUT,
        )
        file.seek(0)
        try:
            result = client.instream(file)
        finally:
            file.seek(0)
    except (clamd.ClamdError, OSError) as exc:
        raise AntivirusUnavailable(str(exc)) from exc

    status, signature = result.get("stream", (None, None))
    if status == "FOUND":
        raise MalwareDetected(signature)
    if status != "OK":
        raise AntivirusUnavailable(f"Неожиданный ответ clamd: {result!r}")


def needs_scan(field_file) -> bool:
    """True — поле содержит новую, ещё НЕ сохранённую в хранилище
    загрузку (Django FieldFile._committed=False — присвоен File/
    UploadedFile, а не строка-имя уже существующего файла). Строковое имя
    (в т.ч. в тестах/фикстурах, при загрузке существующей записи из БД)
    ничего нового не добавляет в хранилище — сканировать нечего."""
    return bool(field_file) and not field_file._committed


def scan_uploaded_field(field_file, *, object_type: str, object_id: str) -> None:
    """Оборачивает scan_file() для незакоммиченного FileField-значения
    (см. needs_scan()) аудитом: при обнаружении сигнатуры пишет
    UPLOAD_MALWARE_DETECTED в WORM-журнал и пробрасывает MalwareDetected
    дальше — вызывающий save() обязан прерваться, не записывать файл.
    AntivirusUnavailable здесь аудит НЕ пишет — это отказ инфраструктуры,
    а не событие безопасности документа; он просто пробрасывается дальше."""
    try:
        scan_file(field_file.file)
    except MalwareDetected as exc:
        from apps.audit.models import AuditLog

        AuditLog.objects.create(
            event_type=AuditLog.EventType.UPLOAD_MALWARE_DETECTED,
            object_type=object_type, object_id=object_id,
            details={"field": field_file.field.name, "signature": exc.signature},
        )
        raise
