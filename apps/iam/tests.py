from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Department


class DepartmentTreeValidationTests(TestCase):
    """Правила 4-уровневого дерева оргструктуры (ТЗ 4.6)."""

    def test_head_office_cannot_have_parent(self):
        other_head = Department(name="Второй аппарат", level=Department.Level.HEAD_OFFICE)
        other_head.full_clean()
        other_head.save()

        head_office = Department(
            name="Аппарат управления 2", level=Department.Level.HEAD_OFFICE, parent=other_head
        )
        with self.assertRaises(ValidationError):
            head_office.full_clean()

    def test_non_head_office_requires_parent(self):
        service = Department(name="Служба без родителя", level=Department.Level.SERVICE)
        with self.assertRaises(ValidationError):
            service.full_clean()

    def test_parent_must_be_one_level_above(self):
        head_office = Department.objects.create(
            name="Аппарат управления", level=Department.Level.HEAD_OFFICE
        )
        # Парк (уровень 3) напрямую под Аппаратом (уровень 1) — пропуск уровня.
        depot = Department(name="Парк №1", level=Department.Level.DEPOT, parent=head_office)
        with self.assertRaises(ValidationError):
            depot.full_clean()

    def test_valid_four_level_chain(self):
        head_office = Department.objects.create(
            name="Аппарат управления", level=Department.Level.HEAD_OFFICE
        )
        service = Department.objects.create(
            name="Служба движения", level=Department.Level.SERVICE, parent=head_office
        )
        depot = Department.objects.create(
            name="Трамвайный парк №1", level=Department.Level.DEPOT, parent=service
        )
        site = Department(
            name="Участок №1", level=Department.Level.SITE, parent=depot
        )
        site.full_clean()  # не должно бросать исключение
        site.save()
        self.assertEqual(site.parent, depot)
