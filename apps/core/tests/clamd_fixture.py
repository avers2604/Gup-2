"""Живой clamd для тестов apps/core/antivirus.py и его интеграций
(apps/documents, apps/templates_bank) — собственная минимальная сигнатура
на EICAR-тестовую строку (стандартный безопасный тест антивирусов,
industry standard, НЕ настоящий вирус — см. https://www.eicar.org/),
без freshclam и обращения к внешним базам: быстро и детерминированно что
локально, что в CI (.github/workflows/ci.yml устанавливает clamav-daemon
тем же способом, что apps/documents/tests/test_ocr.py — tesseract-ocr).

Демон стартует один раз на класс тестов (ClamdTestServer.start() из
setUpClassWithClamd — старт занимает ~1-2 секунды, неприемлемо на каждый
тест), слушает TCP на localhost на нестандартном порту (не 3310 —
не конфликтует с реальным ClamAV из docker-compose.yml, если он тоже
почему-то поднят в этом окружении)."""
from __future__ import annotations

import hashlib
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from django.test import TestCase, override_settings

# Стандартная тестовая строка антивирусной индустрии — не вредоносный код,
# исполнить её нельзя, это просто последовательность байт, которую
# сигнатуры антивирусов договорились детектировать для тестов.
EICAR_BYTES = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

CLAMD_BINARY = shutil.which("clamd") or "/usr/sbin/clamd"
TEST_PORT = 13310


class ClamdTestServer:
    def __init__(self, port: int = TEST_PORT):
        self.port = port
        self._tmpdir: str | None = None
        self._process: subprocess.Popen | None = None

    def start(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="clamd-test-")
        db_dir = Path(self._tmpdir) / "db"
        db_dir.mkdir()

        # ClamAV .hdb requires MD5+size as its signature format. This digest
        # is deliberately not used for authentication/integrity/security.
        md5 = hashlib.md5(EICAR_BYTES, usedforsecurity=False).hexdigest()
        (db_dir / "eicar.hdb").write_text(f"{md5}:{len(EICAR_BYTES)}:Eicar-Test-Signature\n")

        conf_path = Path(self._tmpdir) / "clamd.conf"
        conf_path.write_text(
            f"LocalSocket {self._tmpdir}/clamd.sock\n"
            f"TCPSocket {self.port}\n"
            "TCPAddr 127.0.0.1\n"
            f"DatabaseDirectory {db_dir}\n"
            f"PidFile {self._tmpdir}/clamd.pid\n"
            f"LogFile {self._tmpdir}/clamd.log\n"
            "Foreground true\n"
        )

        self._process = subprocess.Popen(
            [CLAMD_BINARY, "-c", str(conf_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._wait_until_ready()

    def _wait_until_ready(self, timeout: float = 30) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(
                    f"clamd завершился раньше готовности (код {self._process.returncode}) — "
                    f"см. {self._tmpdir}/clamd.log"
                )
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    return
            except OSError:
                time.sleep(0.3)
        raise RuntimeError("clamd не поднялся вовремя для тестов")

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        if self._tmpdir is not None:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None


class ClamdTestCase(TestCase):
    """Django TestCase с живым clamd на весь класс — settings.CLAMAV_PORT
    подменяется на тестовый порт сервера через override_settings, снаружи
    выглядит как обычный CLAMAV_HOST/PORT."""

    clamd_server: ClamdTestServer
    _settings_override: override_settings

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clamd_server = ClamdTestServer()
        cls.clamd_server.start()
        cls._settings_override = override_settings(
            CLAMAV_HOST="127.0.0.1", CLAMAV_PORT=cls.clamd_server.port,
        )
        cls._settings_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._settings_override.disable()
        cls.clamd_server.stop()
        super().tearDownClass()
