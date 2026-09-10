"""Web GUI графа связей версионности (ТЗ 4.2.2).

До этой партии рёбра графа заводились только через Django admin — при
том, что «Отменяет»/«Принят взамен» методист проставляет постоянно.
"""
from django.test import Client, TestCase
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.iam.models import User

from .. import permissions, services
from ..models import DocumentRelation, NormativeDocument
from .factories import make_document
from .test_permissions import make_user

PASSWORD = "Sup3r$ecret!Pass"


class RelationPermissionTests(TestCase):
    def setUp(self):
        self.draft = make_document(reg_number="R1-п")
        self.active = make_document(reg_number="R2-п", status=NormativeDocument.Status.ACTIVE)

    def test_reader_cannot_manage(self):
        self.assertFalse(permissions.can_manage_relations(make_user(), self.draft))

    def test_methodist_can_manage_draft(self):
        user = make_user(role=User.Role.METHODIST)
        self.assertTrue(permissions.can_manage_relations(user, self.draft))

    def test_status_does_not_restrict_relations(self):
        # В отличие от правки полей: забытую ссылку приходится дописывать
        # и к уже действующему документу.
        user = make_user(role=User.Role.METHODIST)
        self.assertTrue(permissions.can_manage_relations(user, self.active))
        self.assertFalse(permissions.can_edit_document(user, self.active))

    def test_restricted_document_requires_clearance(self):
        secret = make_document(
            reg_number="R3-дсп", access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        user = make_user(role=User.Role.METHODIST)
        self.assertFalse(permissions.can_manage_relations(user, secret))


class AddRelationServiceTests(TestCase):
    def setUp(self):
        self.actor = make_user(personnel_number="0200", role=User.Role.METHODIST)
        self.source = make_document(reg_number="R10-п")
        self.target = make_document(reg_number="R11-п")

    def test_creates_relation_and_audit_entry(self):
        services.add_relation(
            actor=self.actor, from_document=self.source, to_document=self.target,
            relation_type=DocumentRelation.RelationType.CANCELS, note="Пункт 3.2",
        )
        entry = AuditLog.objects.get(event_type=AuditLog.EventType.DOCUMENT_RELATION_ADDED)
        self.assertEqual(entry.object_id, "R10-п")
        self.assertEqual(entry.details["to_document"], "R11-п")
        self.assertEqual(entry.actor_personnel_number, "0200")

    def test_removal_is_also_logged(self):
        relation = services.add_relation(
            actor=self.actor, from_document=self.source, to_document=self.target,
            relation_type=DocumentRelation.RelationType.REFERENCES,
        )
        services.remove_relation(actor=self.actor, relation=relation)
        self.assertTrue(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.DOCUMENT_RELATION_REMOVED
            ).exists()
        )
        self.assertEqual(DocumentRelation.objects.count(), 0)

    def test_cannot_link_to_invisible_document(self):
        # Иначе гриф «ДСП» утекает через сам факт успешного создания связи.
        from django.core.exceptions import PermissionDenied

        secret = make_document(
            reg_number="R12-дсп", access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        with self.assertRaises(PermissionDenied):
            services.add_relation(
                actor=self.actor, from_document=self.source, to_document=secret,
                relation_type=DocumentRelation.RelationType.REFERENCES,
            )

    def test_cycle_is_rejected(self):
        from django.core.exceptions import ValidationError

        services.add_relation(
            actor=self.actor, from_document=self.source, to_document=self.target,
            relation_type=DocumentRelation.RelationType.REFERENCES,
        )
        with self.assertRaises(ValidationError):
            services.add_relation(
                actor=self.actor, from_document=self.target, to_document=self.source,
                relation_type=DocumentRelation.RelationType.REFERENCES,
            )


class RelationViewTests(TestCase):
    def setUp(self):
        self.methodist = make_user(personnel_number="0210", role=User.Role.METHODIST)
        self.reader = make_user(personnel_number="0211")
        self.source = make_document(reg_number="R20-п")
        self.target = make_document(reg_number="R21-п")

    def _client(self, user):
        client = Client()
        client.login(personnel_number=user.personnel_number, password=PASSWORD)
        return client

    def test_reader_cannot_open_form(self):
        response = self._client(self.reader).get(
            reverse("documents:relation_create", args=[self.source.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_form_excludes_the_document_itself(self):
        response = self._client(self.methodist).get(
            reverse("documents:relation_create", args=[self.source.pk])
        )
        self.assertEqual(response.status_code, 200)
        choices = response.context["form"].fields["to_document"].queryset
        self.assertNotIn(self.source, choices)
        self.assertIn(self.target, choices)

    def test_form_hides_restricted_documents(self):
        make_document(
            reg_number="R22-дсп", access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        response = self._client(self.methodist).get(
            reverse("documents:relation_create", args=[self.source.pk])
        )
        self.assertNotContains(response, "R22-дсп")

    def test_creates_relation(self):
        response = self._client(self.methodist).post(
            reverse("documents:relation_create", args=[self.source.pk]),
            {
                "to_document": str(self.target.pk),
                "relation_type": DocumentRelation.RelationType.CANCELS,
                "note": "Отменён пункт 3.2",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            DocumentRelation.objects.filter(
                from_document=self.source, to_document=self.target
            ).exists()
        )

    def test_duplicate_relation_reports_readable_error(self):
        client = self._client(self.methodist)
        payload = {
            "to_document": str(self.target.pk),
            "relation_type": DocumentRelation.RelationType.CANCELS,
            "note": "",
        }
        client.post(reverse("documents:relation_create", args=[self.source.pk]), payload)
        response = client.post(
            reverse("documents:relation_create", args=[self.source.pk]), payload
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "уже заведена")

    def test_cycle_reports_readable_error(self):
        DocumentRelation.objects.create(
            from_document=self.target, to_document=self.source,
            relation_type=DocumentRelation.RelationType.REFERENCES,
        )
        response = self._client(self.methodist).post(
            reverse("documents:relation_create", args=[self.source.pk]),
            {
                "to_document": str(self.target.pk),
                "relation_type": DocumentRelation.RelationType.REFERENCES,
                "note": "",
            },
        )
        self.assertContains(response, "цикл")

    def test_delete_requires_post(self):
        relation = DocumentRelation.objects.create(
            from_document=self.source, to_document=self.target,
            relation_type=DocumentRelation.RelationType.REFERENCES,
        )
        url = reverse("documents:relation_delete", args=[self.source.pk, relation.pk])
        self.assertEqual(self._client(self.methodist).get(url).status_code, 405)
        self.assertTrue(DocumentRelation.objects.filter(pk=relation.pk).exists())

    def test_delete_removes_relation(self):
        relation = DocumentRelation.objects.create(
            from_document=self.source, to_document=self.target,
            relation_type=DocumentRelation.RelationType.REFERENCES,
        )
        response = self._client(self.methodist).post(
            reverse("documents:relation_delete", args=[self.source.pk, relation.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(DocumentRelation.objects.filter(pk=relation.pk).exists())

    def test_cannot_delete_relation_of_another_document(self):
        # relation_id из чужой карточки не должен сниматься через URL
        # своей — иначе проверка прав ничего не значит.
        other = make_document(reg_number="R23-п")
        relation = DocumentRelation.objects.create(
            from_document=other, to_document=self.target,
            relation_type=DocumentRelation.RelationType.REFERENCES,
        )
        response = self._client(self.methodist).post(
            reverse("documents:relation_delete", args=[self.source.pk, relation.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(DocumentRelation.objects.filter(pk=relation.pk).exists())
