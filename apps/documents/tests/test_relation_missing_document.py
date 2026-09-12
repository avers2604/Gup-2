from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.documents import services
from apps.documents.models import DocumentRelation
from apps.iam.models import User

from .factories import make_document
from .test_permissions import make_user


class RelationMissingDocumentTests(TestCase):
    """Stale relation form data must become a domain validation error, not 500."""

    def test_deleted_target_is_reported_as_validation_error(self):
        methodist = make_user(
            personnel_number="0541", role=User.Role.METHODIST
        )
        source = make_document(reg_number="REL-MISSING-1")
        target = make_document(reg_number="REL-MISSING-2")
        target.delete()

        with self.assertRaises(ValidationError) as error:
            services.add_relation(
                actor=methodist,
                from_document=source,
                to_document=target,
                relation_type=DocumentRelation.RelationType.REFERENCES,
            )

        self.assertIn("удал", str(error.exception).lower())
