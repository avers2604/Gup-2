from django.core.exceptions import ValidationError
from django.test import TestCase

from ..models import Department


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

    def test_parent_cannot_be_lower_in_hierarchy(self):
        # Перевёрнутая иерархия: узел уровня 3 не может быть родителем узла
        # уровня 2 (родитель обязан быть РОВНО на уровень выше, а не просто
        # «выше по номеру» и не ниже) — та же проверка, что и пропуск
        # уровня, но с другой стороны.
        head_office = Department.objects.create(
            name="Аппарат управления", level=Department.Level.HEAD_OFFICE
        )
        service = Department.objects.create(
            name="Служба движения", level=Department.Level.SERVICE, parent=head_office
        )
        depot = Department.objects.create(
            name="Трамвайный парк №1", level=Department.Level.DEPOT, parent=service
        )
        inverted_service = Department(
            name="Служба с перевёрнутым родителем", level=Department.Level.SERVICE, parent=depot
        )
        with self.assertRaises(ValidationError):
            inverted_service.full_clean()

    def test_validation_is_structural_not_name_based(self):
        # ТЗ 4.6.1 называет конкретные уровни («Аппарат управления»,
        # «Служба» и т.д.), но это только человекочитаемые label у
        # Department.Level — само правило родитель-на-уровень-выше
        # оперирует только числами уровня, поэтому переименование служб
        # на Этапе 1 (обследование) не потребует правки кода валидации.
        head_office = Department.objects.create(
            name="Головной офис (временное имя до обследования)",
            level=Department.Level.HEAD_OFFICE,
        )
        service = Department(
            name="Служба X (будет переименована)",
            level=Department.Level.SERVICE,
            parent=head_office,
        )
        service.full_clean()  # имена никак не участвуют в проверке — не должно упасть
        service.save()
        self.assertEqual(service.parent, head_office)
