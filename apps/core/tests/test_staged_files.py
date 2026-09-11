from __future__ import annotations

import io
from datetime import timedelta
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from apps.core.models import StagedFilePromotion, TaskOutbox
from apps.core.staged_files import _copy_s3, promote_staged_file_once
from apps.documents.tests.factories import make_document


class MemoryStorage(Storage):
    def __init__(self):
        self.objects = {}

    def _open(self, name, mode="rb"):
        return ContentFile(self.objects[name], name=name)

    def _save(self, name, content):
        if hasattr(content, "seek"):
            content.seek(0)
        self.objects[name] = content.read()
        return name

    def exists(self, name):
        return name in self.objects

    def delete(self, name):
        self.objects.pop(name, None)


class StagedPromotionIntegrationTests(TestCase):
    def setUp(self):
        self.working = MemoryStorage()
        self.originals = MemoryStorage()
        self.storage_patches = (
            patch("apps.core.staged_files.working_storage", return_value=self.working),
            patch("apps.core.staged_files.originals_storage", return_value=self.originals),
        )
        for item in self.storage_patches:
            item.start()
            self.addCleanup(item.stop)
        self.scan_patch = patch("apps.core.antivirus.needs_scan", return_value=False)
        self.scan_patch.start()
        self.addCleanup(self.scan_patch.stop)
        self.document = make_document(reg_number="WORM-STAGE-1", files_original="old.pdf")

    def _replace_original(self):
        self.document.files_original = SimpleUploadedFile(
            "new.pdf", b"immutable-payload", content_type="application/pdf"
        )
        self.document.save()
        self.document.refresh_from_db()
        return StagedFilePromotion.objects.get(object_id=str(self.document.pk))

    def test_new_original_is_only_in_mutable_staging_before_commit_promotion(self):
        promotion = self._replace_original()
        self.assertIn(promotion.staging_name, self.working.objects)
        self.assertNotIn(promotion.destination_name, self.originals.objects)
        self.assertEqual(self.document.files_original.name, promotion.destination_name)
        self.assertTrue(
            TaskOutbox.objects.filter(
                task_name="apps.core.tasks.promote_staged_file",
                args=[str(promotion.pk)],
            ).exists()
        )

    def test_database_rollback_never_creates_worm_or_durable_promotion(self):
        before = StagedFilePromotion.objects.count()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.document.files_original = SimpleUploadedFile(
                    "rollback.pdf", b"rollback-payload", content_type="application/pdf"
                )
                self.document.save()
                raise RuntimeError("force rollback")

        self.assertEqual(StagedFilePromotion.objects.count(), before)
        self.assertFalse(self.originals.objects)
        # A mutable staging orphan is acceptable and is cleaned by the bucket
        # lifecycle rule; the critical invariant is that WORM was untouched.
        self.assertTrue(any(name.startswith("staging/worm/") for name in self.working.objects))

    def test_promotion_verifies_bytes_updates_sha_and_deletes_staging(self):
        promotion = self._replace_original()
        with self.captureOnCommitCallbacks(execute=True):
            digest = promote_staged_file_once(promotion.pk)

        promotion.refresh_from_db()
        self.document.refresh_from_db()
        self.assertEqual(promotion.sha256, digest)
        self.assertIsNotNone(promotion.completed_at)
        self.assertEqual(self.document.files_original_sha256, digest)
        self.assertEqual(self.originals.objects[promotion.destination_name], b"immutable-payload")
        self.assertNotIn(promotion.staging_name, self.working.objects)


class ObjectLockCopyTests(TestCase):
    def _storage(self, bucket, payload):
        storage = MagicMock()
        storage.bucket_name = bucket
        storage.open.side_effect = lambda name, mode="rb": io.BytesIO(payload)
        return storage

    @staticmethod
    def _missing_key():
        return ClientError(
            {
                "Error": {"Code": "NoSuchKey", "Message": "missing"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "HeadObject",
        )

    def test_fixed_retention_is_sent_and_verified_on_copy(self):
        retain_until = timezone.now() + timedelta(days=30)
        promotion = StagedFilePromotion(
            model_label="documents.normativedocument",
            object_id="1",
            field_name="files_original",
            staging_name="staging/source.pdf",
            destination_name="documents/originals/final.pdf",
            lock_mode="COMPLIANCE",
            retain_until=retain_until,
            legal_hold=False,
        )
        source = self._storage("working", b"payload")
        destination = self._storage("originals", b"payload")
        client = MagicMock()
        destination.connection.meta.client = client
        client.head_object.side_effect = [
            self._missing_key(),
            {"ContentType": "application/pdf", "Metadata": {}},
            {
                "Metadata": {"bz-promotion-id": str(promotion.pk)},
                "ObjectLockMode": "COMPLIANCE",
                "ObjectLockRetainUntilDate": retain_until,
            },
        ]
        client.get_object_lock_configuration.return_value = {
            "ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}
        }

        _copy_s3(promotion, source, destination)
        kwargs = client.copy_object.call_args.kwargs
        self.assertEqual(kwargs["ObjectLockMode"], "COMPLIANCE")
        self.assertEqual(kwargs["ObjectLockRetainUntilDate"], retain_until)
        self.assertNotIn("ObjectLockLegalHoldStatus", kwargs)

    def test_permanent_retention_uses_legal_hold(self):
        promotion = StagedFilePromotion(
            model_label="templates_bank.template",
            object_id="1",
            field_name="file_sample",
            staging_name="staging/sample.pdf",
            destination_name="templates/samples/sample.pdf",
            lock_mode="",
            retain_until=None,
            legal_hold=True,
        )
        source = self._storage("working", b"payload")
        destination = self._storage("originals", b"payload")
        client = MagicMock()
        destination.connection.meta.client = client
        client.head_object.side_effect = [
            self._missing_key(),
            {"ContentType": "application/pdf", "Metadata": {}},
            {
                "Metadata": {"bz-promotion-id": str(promotion.pk)},
                "ObjectLockLegalHoldStatus": "ON",
            },
        ]
        client.get_object_lock_configuration.return_value = {
            "ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}
        }

        _copy_s3(promotion, source, destination)
        kwargs = client.copy_object.call_args.kwargs
        self.assertEqual(kwargs["ObjectLockLegalHoldStatus"], "ON")
        self.assertNotIn("ObjectLockMode", kwargs)
        self.assertNotIn("ObjectLockRetainUntilDate", kwargs)
