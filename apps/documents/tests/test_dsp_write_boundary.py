"""Регрессии границы записи документов с грифом ДСП."""

import datetime

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from apps.documents import services
from apps.documents.models import NormativeDocument
from apps.iam.models import User

from .factories import make_document
from .test_permissions import make_user


class DspWriteBoundaryTests(TestCase):
    def setUp(self):
        self.methodist = make_user(
            personnel_number="0540",
            role=User.Role.METHODIST,
            dsp_access=False,
        )
        self.general = make_document(
            reg_number="DSP-WRITE-BASE",
            files_original="documents/originals/2026/01/base.pdf",
        )

    def test_editor_without_clearance_cannot_create_restricted_document(self):
        """Право создавать карточки не должно выдавать право создавать ДСП."""
        with self.assertRaises(PermissionDenied):
            services.create_document(
                actor=self.methodist,
                reg_number="DSP-CREATE-DENIED",
                reg_date=datetime.date(2026, 9, 1),
                effective_date=datetime.date(2026, 9, 2),
                doc_type=NormativeDocument.DocType.ORDER,
                title="Закрытый документ",
                issuer_dept=self.general.issuer_dept,
                access_level=NormativeDocument.AccessLevel.RESTRICTED,
                retention_category=self.general.retention_category,
                files_original="documents/originals/2026/09/restricted.pdf",
            )

        self.assertFalse(
            NormativeDocument.objects.filter(reg_number="DSP-CREATE-DENIED").exists()
        )

    def test_editor_without_clearance_cannot_reclassify_draft_as_restricted(self):
        """Общий черновик нельзя превратить в невидимый себе ДСП-документ."""
        with self.assertRaises(PermissionDenied):
            services.update_document(
                actor=self.methodist,
                document=self.general,
                access_level=NormativeDocument.AccessLevel.RESTRICTED,
            )

        self.general.refresh_from_db()
        self.assertEqual(
            self.general.access_level,
            NormativeDocument.AccessLevel.GENERAL,
        )
