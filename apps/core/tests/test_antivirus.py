"""apps/core/antivirus.py — реальный (не замоканный) прогон против живого
clamd (apps/core/tests/clamd_fixture.py, EICAR-тестовая сигнатура)."""
import io

from django.test import override_settings

from apps.core.antivirus import AntivirusUnavailable, MalwareDetected, scan_file

from .clamd_fixture import EICAR_BYTES, ClamdTestCase


class ScanFileTests(ClamdTestCase):
    def test_clean_file_does_not_raise(self):
        scan_file(io.BytesIO(b"just a normal document, nothing malicious here"))

    def test_eicar_raises_malware_detected(self):
        with self.assertRaises(MalwareDetected) as ctx:
            scan_file(io.BytesIO(EICAR_BYTES))
        self.assertIn("Eicar-Test-Signature", ctx.exception.signature)

    def test_file_position_restored_after_scan(self):
        file = io.BytesIO(b"clean content")
        scan_file(file)
        self.assertEqual(file.tell(), 0)

    def test_unreachable_clamd_raises_antivirus_unavailable(self):
        with override_settings(CLAMAV_HOST="127.0.0.1", CLAMAV_PORT=1):
            with self.assertRaises(AntivirusUnavailable):
                scan_file(io.BytesIO(b"whatever"))


class StreamMaxLengthTests(ClamdTestCase):
    """Проверяет вживую (реальный clamd, не мок) честную границу из
    deploy/clamav/clamd.conf: заводской дефолт ClamAV (StreamMaxLength
    25M) меньше лимита ТЗ 1.2 §4.2.1 (150 МБ) — что именно происходит,
    когда поток БОЛЬШЕ настроенного лимита. Отдельный порт и намеренно
    маленький лимит (1M), чтобы не гонять реальные мегабайты в тесте."""

    clamd_port = 13311
    clamd_stream_max_length = "1M"

    def test_stream_within_limit_is_scanned_normally(self):
        # Не должно раниться — поток меньше лимита сканируется как обычно.
        scan_file(io.BytesIO(b"A" * (500 * 1024)))

    def test_stream_over_limit_fails_closed_not_silently_clean(self):
        # КЛЮЧЕВАЯ проверка риска из ревью: превышение StreamMaxLength не
        # должно молча вернуть "чисто" (ложноотрицательный результат) —
        # clamd рвёт соединение, clamd-клиент поднимает OSError
        # (BrokenPipeError), scan_file() ловит это и требует
        # AntivirusUnavailable (fail-closed), а не MalwareDetected=нет.
        with self.assertRaises(AntivirusUnavailable):
            scan_file(io.BytesIO(b"C" * (2 * 1024 * 1024)))
