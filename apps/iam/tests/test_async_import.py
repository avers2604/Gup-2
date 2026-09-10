import io
import tempfile
from unittest import mock

import openpyxl
from django.core.files.storage import FileSystemStorage
from django.test import TestCase

from apps.iam.models import Department, User
from apps.iam.services import HEADER

from ..async_import import stage_personnel_import
from ..tasks import run_personnel_import_task


def _xlsx_bytes(department_path: str) -> io.BytesIO:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(HEADER)
    values = {
        "tab_number": "async-001",
        "last_name": "Иванов",
        "first_name": "Пётр",
        "middle_name": "",
        "position": "Водитель",
        "department_path": department_path,
        "role": "reader",
        "dsp_access": "false",
        "email": "",
        "status": "active",
    }
    sheet.append([values.get(column, "") for column in HEADER])
    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


class AsyncPersonnelImportTests(TestCase):
    def setUp(self):
        self.head, _ = Department.objects.get_or_create(
            name="Асинхронный аппарат",
            parent=None,
            defaults={"level": Department.Level.HEAD_OFFICE},
        )
        self.service, _ = Department.objects.get_or_create(
            name="Асинхронная служба",
            parent=self.head,
            defaults={"level": Department.Level.SERVICE},
        )
        self.department_path = f"{self.head.name}/{self.service.name}"

    def test_task_imports_staged_file_and_removes_source(self):
        with tempfile.TemporaryDirectory() as location:
            storage = FileSystemStorage(location=location)
            with mock.patch("apps.iam.async_import.working_storage", return_value=storage):
                storage_name = stage_personnel_import(
                    _xlsx_bytes(self.department_path),
                    original_name="personnel.xlsx",
                )

            self.assertTrue(storage.exists(storage_name))
            with mock.patch("apps.iam.tasks.working_storage", return_value=storage):
                result = run_personnel_import_task.run(
                    storage_name=storage_name,
                    actor_id=None,
                )

            self.assertEqual(result["created"], 1)
            self.assertEqual(result["errors"], 0)
            self.assertTrue(User.objects.filter(personnel_number="async-001").exists())
            self.assertFalse(storage.exists(storage_name))
