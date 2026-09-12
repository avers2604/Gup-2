import tempfile
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import DatabaseError
from django.test import TestCase

from ..tasks import run_personnel_import_task


class PersonnelImportFailureRetentionTests(TestCase):
    def test_database_failure_preserves_staged_source_for_recovery(self):
        with tempfile.TemporaryDirectory() as location:
            storage = FileSystemStorage(location=location)
            storage_name = storage.save("imports/personnel.xlsx", ContentFile(b"xlsx"))

            with (
                mock.patch("apps.iam.tasks.working_storage", return_value=storage),
                mock.patch(
                    "apps.iam.services.import_personnel",
                    side_effect=DatabaseError("primary switched"),
                ),
            ):
                with self.assertRaises(DatabaseError):
                    run_personnel_import_task.run(storage_name=storage_name, actor_id=None)

            self.assertTrue(storage.exists(storage_name))
