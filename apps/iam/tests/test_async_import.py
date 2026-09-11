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

TEST_PERSONNEL_NUMBER = "909001"


def _xlsx_bytes(department_path: str) -> io.BytesIO:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(HEADER)
    values = {
        "tab_number": TEST_PERSONNEL_NUMBER,
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
            self.assertTrue(
                User.objects.filter(personnel_number=TEST_PERSONNEL_NUMBER).exists()
            )
            self.assertFalse(storage.exists(storage_name))

    def test_duplicate_delivery_does_not_import_twice(self):
        """acks_late=True означает, что задача может быть доставлена повторно
        (воркер умер после выполнения, но до подтверждения). Импорт при этом
        нельзя выполнять второй раз: он ещё раз перезапишет учётные записи и
        повторно поднимет события смены роли в WORM-журнале."""
        with tempfile.TemporaryDirectory() as location:
            storage = FileSystemStorage(location=location)
            with mock.patch("apps.iam.async_import.working_storage", return_value=storage):
                storage_name = stage_personnel_import(
                    _xlsx_bytes(self.department_path),
                    original_name="personnel.xlsx",
                )

            with mock.patch("apps.iam.tasks.working_storage", return_value=storage):
                run_personnel_import_task.run(storage_name=storage_name, actor_id=None)
                user = User.objects.get(personnel_number=TEST_PERSONNEL_NUMBER)
                touched_at = user.updated_at if hasattr(user, "updated_at") else None

                # Та же задача приходит второй раз — staged-файла уже нет.
                repeat = run_personnel_import_task.run(
                    storage_name=storage_name, actor_id=None,
                )

            self.assertTrue(repeat["duplicate"])
            self.assertNotIn("created", repeat)
            self.assertEqual(
                User.objects.filter(personnel_number=TEST_PERSONNEL_NUMBER).count(), 1
            )
            if touched_at is not None:
                user.refresh_from_db()
                self.assertEqual(user.updated_at, touched_at)
