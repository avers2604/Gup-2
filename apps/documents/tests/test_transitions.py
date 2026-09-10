"""Граф допустимых переходов статуса (apps/documents/transitions.py).

Как и матрица прав, граф выведен, а не процитирован из ТЗ — см. docstring
модуля. Эти тесты фиксируют его как контракт: если Заказчик уточнит
жизненный цикл документа, они меняются вместе с графом.
"""
from django.test import SimpleTestCase

from .. import transitions
from ..models import NormativeDocument

Status = NormativeDocument.Status


class AllowedTransitionsTests(SimpleTestCase):
    def test_draft_is_published(self):
        self.assertTrue(transitions.is_allowed(Status.DRAFT, Status.ACTIVE))

    def test_draft_can_be_archived(self):
        # Отклонённый черновик: иначе у него нет ни одного выхода —
        # удаление в системе с WORM-журналом не предусмотрено.
        self.assertTrue(transitions.is_allowed(Status.DRAFT, Status.ARCHIVED))

    def test_active_document_is_not_returned_to_draft(self):
        # Снятие юридической силы — это «Утратил силу», а не откат в
        # черновик.
        self.assertFalse(transitions.is_allowed(Status.ACTIVE, Status.DRAFT))

    def test_active_document_is_not_archived_directly(self):
        self.assertFalse(transitions.is_allowed(Status.ACTIVE, Status.ARCHIVED))
        self.assertTrue(transitions.is_allowed(Status.ACTIVE, Status.REVOKED))
        self.assertTrue(transitions.is_allowed(Status.REVOKED, Status.ARCHIVED))

    def test_amended_only_goes_to_revoked(self):
        self.assertEqual(transitions.allowed_targets(Status.ACTIVE_AMENDED), frozenset({Status.REVOKED}))

    def test_archived_is_terminal(self):
        self.assertEqual(transitions.allowed_targets(Status.ARCHIVED), frozenset())

    def test_unknown_status_allows_nothing(self):
        # Перечень статусов может расшириться миграцией раньше, чем сюда
        # допишут переходы — запретить безопаснее, чем разрешить наугад.
        self.assertEqual(transitions.allowed_targets("нет_такого_статуса"), frozenset())

    def test_no_status_transitions_to_itself(self):
        for status in Status.values:
            with self.subTest(status=status):
                self.assertNotIn(status, transitions.allowed_targets(status))

    def test_every_status_is_covered_by_the_graph(self):
        # Статус, добавленный в модель и забытый здесь, молча стал бы
        # тупиком: карточка в нём зависла бы без единого перехода.
        self.assertEqual(set(transitions.ALLOWED_TRANSITIONS), set(Status.values))

    def test_target_choices_are_labelled_and_ordered(self):
        choices = transitions.target_choices(Status.ACTIVE)
        self.assertEqual(
            choices,
            [(Status.ACTIVE_AMENDED, "Действует с изм."), (Status.REVOKED, "Утратил силу")],
        )
