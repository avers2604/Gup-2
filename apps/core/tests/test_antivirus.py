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
