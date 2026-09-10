"""Граф допустимых переходов статуса (apps/documents/transitions.py).

Граф выведен, а не процитирован из ТЗ, и **Заказчик его уточнил**: см.
docstring модуля. Тесты ниже переписаны под уточнение — в частности,
`Действует → Черновик` теперь разрешён (откат ошибочной публикации), а
из «Действует с изм.» есть выход обратно в «Действует».

Эти тесты фиксируют граф как контракт: следующее уточнение снова меняет
их вместе с графом.
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

    def test_active_document_can_be_rolled_back_to_draft(self):
        # Уточнение Заказчика: ошибочная публикация (не тот файл, не та
        # дата) откатывается в черновик. Оформлять её через «Утратил
        # силу» было бы неправдой — документ не должен был действовать.
        self.assertTrue(transitions.is_allowed(Status.ACTIVE, Status.DRAFT))

    def test_rollback_and_annulment_are_administrator_only(self):
        # Оба перехода отменяют юридически значимое действие, и тот, кто
        # публиковал, не должен бесследно править собственную ошибку.
        self.assertTrue(transitions.requires_administrator(Status.DRAFT))
        self.assertTrue(transitions.requires_administrator(Status.ANNULLED))
        self.assertFalse(transitions.requires_administrator(Status.ACTIVE))
        self.assertFalse(transitions.requires_administrator(Status.REVOKED))

    def test_rollback_and_annulment_require_a_reason(self):
        self.assertTrue(transitions.requires_reason(Status.DRAFT))
        self.assertTrue(transitions.requires_reason(Status.ANNULLED))
        self.assertFalse(transitions.requires_reason(Status.REVOKED))

    def test_annulment_is_distinct_from_revocation(self):
        # «Аннулирован» и «Утратил силу» — разные исходы, оба доступны из
        # действующего документа, и подменять один другим нельзя.
        targets = transitions.allowed_targets(Status.ACTIVE)
        self.assertIn(Status.ANNULLED, targets)
        self.assertIn(Status.REVOKED, targets)

    def test_annulled_document_goes_only_to_archive(self):
        self.assertEqual(
            transitions.allowed_targets(Status.ANNULLED), frozenset({Status.ARCHIVED})
        )

    def test_draft_cannot_be_annulled(self):
        # Аннулировать нечего: публикации не было.
        self.assertFalse(transitions.is_allowed(Status.DRAFT, Status.ANNULLED))

    def test_active_document_is_not_archived_directly(self):
        self.assertFalse(transitions.is_allowed(Status.ACTIVE, Status.ARCHIVED))
        self.assertTrue(transitions.is_allowed(Status.ACTIVE, Status.REVOKED))
        self.assertTrue(transitions.is_allowed(Status.REVOKED, Status.ARCHIVED))

    def test_amended_can_return_to_active(self):
        # Уточнение Заказчика: если все изменяющие документы отменены,
        # базовый снова действует в исходной редакции.
        self.assertTrue(transitions.is_allowed(Status.ACTIVE_AMENDED, Status.ACTIVE))

    def test_amended_targets(self):
        self.assertEqual(
            transitions.allowed_targets(Status.ACTIVE_AMENDED),
            frozenset({Status.ACTIVE, Status.REVOKED, Status.ANNULLED}),
        )

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
        # Порядок — как в Status.choices, а не как во frozenset.
        choices = transitions.target_choices(Status.ACTIVE)
        self.assertEqual(
            choices,
            [
                (Status.DRAFT, "Черновик"),
                (Status.ACTIVE_AMENDED, "Действует с изм."),
                (Status.REVOKED, "Утратил силу"),
                (Status.ANNULLED, "Аннулирован"),
            ],
        )
