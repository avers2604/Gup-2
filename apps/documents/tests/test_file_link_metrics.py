from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.core.models import BusinessMetricCounter
from apps.iam.models import Department, User

from .factories import make_document


class DocumentFileLinkMetricsTests(TestCase):
    def setUp(self):
        dept, _ = Department.objects.get_or_create(
            name="Служба файлов",
            defaults={"level": Department.Level.SERVICE},
        )
        self.user = User.objects.create(
            personnel_number="98002",
            last_name="Файлов",
            first_name="Тест",
            position="Тестировщик",
            department=dept,
            role=User.Role.READER,
        )
        self.client.force_login(self.user)
        self.document = make_document(
            reg_number="FILE-METRIC",
            files_original="documents/originals/2026/01/file.pdf",
        )

    def _counter(self, status):
        row = BusinessMetricCounter.objects.filter(name=f"link_generation_failure_{status}").first()
        return row.value if row else 0

    def test_unknown_link_kind_counts_404(self):
        response = self.client.get(
            reverse("documents:file-link", args=[self.document.pk, "unknown"])
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._counter(404), 1)

    @patch("django.core.files.storage.FileSystemStorage.url", side_effect=PermissionError("forbidden"))
    def test_storage_permission_failure_counts_403(self, _url):
        response = self.client.get(
            reverse("documents:file-link", args=[self.document.pk, "original"])
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._counter(403), 1)

    @patch("django.core.files.storage.FileSystemStorage.url", side_effect=RuntimeError("gateway timeout"))
    def test_unknown_storage_failure_counts_504(self, _url):
        response = self.client.get(
            reverse("documents:file-link", args=[self.document.pk, "original"])
        )
        self.assertEqual(response.status_code, 504)
        self.assertEqual(self._counter(504), 1)
