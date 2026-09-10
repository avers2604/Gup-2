"""Права доступа к карточкам НРД (apps/documents/permissions.py).

Матрица выведена из прежних проверок в admin.py, ROLE_PRIVILEGE_ORDER и
STACK.md — см. docstring модуля прав. Тесты фиксируют её как контракт:
если Заказчик уточнит полномочия, эти проверки — первое, что должно
измениться вместе с матрицей.
"""
from django.test import TestCase

from apps.iam.models import Department, User

from .. import permissions
from ..models import NormativeDocument
from .factories import make_document


def make_user(personnel_number="0001", role=User.Role.READER, **kwargs):
    dept, _ = Department.objects.get_or_create(
        name="Служба движения", defaults={"level": Department.Level.SERVICE}
    )
    defaults = dict(
        personnel_number=personnel_number, last_name="Иванов", first_name="Пётр",
        position="Специалист", department=dept, role=role,
        status=User.Status.ACTIVE,
    )
    defaults.update(kwargs)
    user = User(**defaults)
    user.set_password("Sup3r$ecret!Pass")
    user.save()
    return user


class DspAccessTests(TestCase):
    """Гриф ДСП — признак учётной записи, не роль: допуск оформляется
    отдельно от должности."""

    def setUp(self):
        self.general = make_document(reg_number="1-общий")
        self.restricted = make_document(
            reg_number="2-дсп", access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )

    def test_reader_without_clearance_sees_only_general(self):
        user = make_user()
        self.assertTrue(permissions.can_view_document(user, self.general))
        self.assertFalse(permissions.can_view_document(user, self.restricted))

    def test_reader_with_clearance_sees_restricted(self):
        user = make_user(dsp_access=True)
        self.assertTrue(permissions.can_view_document(user, self.restricted))

    def test_high_role_without_clearance_still_blocked(self):
        # Контролёр/Юрист без допуска ДСП не видит документ с грифом:
        # допуск не наследуется от уровня роли.
        user = make_user(role=User.Role.CONTROLLER_LAWYER)
        self.assertFalse(permissions.can_view_document(user, self.restricted))

    def test_superuser_sees_everything(self):
        user = make_user(is_superuser=True)
        self.assertTrue(permissions.can_view_document(user, self.restricted))

    def test_visible_documents_filters_restricted(self):
        user = make_user()
        visible = permissions.visible_documents(user)
        self.assertIn(self.general, visible)
        self.assertNotIn(self.restricted, visible)

    def test_visible_documents_empty_for_anonymous(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertEqual(permissions.visible_documents(AnonymousUser()).count(), 0)


class EditPermissionTests(TestCase):
    def setUp(self):
        self.draft = make_document(reg_number="3-черновик")
        self.active = make_document(
            reg_number="4-действует", status=NormativeDocument.Status.ACTIVE,
        )

    def test_reader_cannot_edit(self):
        self.assertFalse(permissions.can_edit_document(make_user(), self.draft))

    def test_methodist_edits_draft(self):
        user = make_user(role=User.Role.METHODIST)
        self.assertTrue(permissions.can_edit_document(user, self.draft))

    def test_nobody_edits_document_in_force(self):
        # Документ, уже имеющий силу, правится новой редакцией со связью
        # версионности (ТЗ 4.2.2), а не редактированием на месте.
        for role in (User.Role.METHODIST, User.Role.CONTROLLER_LAWYER, User.Role.ADMINISTRATOR):
            with self.subTest(role=role):
                user = make_user(personnel_number=f"90{role[:2]}", role=role)
                self.assertFalse(permissions.can_edit_document(user, self.active))

    def test_editor_without_dsp_cannot_edit_restricted_draft(self):
        restricted_draft = make_document(
            reg_number="5-дсп-черновик",
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        user = make_user(role=User.Role.METHODIST)
        self.assertFalse(permissions.can_edit_document(user, restricted_draft))


class StatusChangePermissionTests(TestCase):
    def setUp(self):
        self.document = make_document(reg_number="6-статус")

    def test_methodist_cannot_change_status(self):
        # Публикация — юридически значимое действие, оно за
        # Контролёром/Юристом (STACK.md).
        user = make_user(role=User.Role.METHODIST)
        self.assertFalse(permissions.can_change_status(user, self.document))

    def test_controller_can_change_status(self):
        user = make_user(role=User.Role.CONTROLLER_LAWYER)
        self.assertTrue(permissions.can_change_status(user, self.document))

    def test_security_officer_cannot_change_status(self):
        # Офицер ИБ выше по ROLE_PRIVILEGE_ORDER, но это параллельная
        # ветка полномочий (см. STACK.md): контроль безопасности, а не
        # придание документам юридической силы.
        user = make_user(role=User.Role.SECURITY_OFFICER)
        self.assertFalse(permissions.can_change_status(user, self.document))
