"""Формы выглядят и озвучиваются одинаково на всех экранах.

Партиал поля заведён именно затем, чтобы «почти такая же» разметка,
скопированная во вторую форму, не разошлась с первой на первой же правке.
Но половина экранов — вход, смена пароля, вычитка, подтверждение фактора
— собирала поле руками и уже разошлась: где-то ошибки поля выводились,
где-то только общие, подсказка не показывалась нигде, aria-invalid не
было ни в одной форме.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from apps.documents.tests.factories import make_document
from apps.documents.tests.test_permissions import PASSWORD, make_user
from apps.iam.models import User

TEMPLATES_DIR = Path(settings.BASE_DIR) / "templates"


class EveryFormUsesTheSharedFieldTests(SimpleTestCase):
    """Ручная разметка поля — источник расхождений, а не стиль."""

    #: Подпись поля, написанная в шаблоне руками. У партиала она внутри,
    #: поэтому снаружи такой конструкции быть не должно.
    _HAND_ROLLED_LABEL = re.compile(r'<label class="field-label"[^>]*>\s*\{\{')

    def test_no_template_builds_a_field_label_by_hand(self):
        offenders = [
            path.relative_to(TEMPLATES_DIR).as_posix()
            for path in sorted(TEMPLATES_DIR.rglob("*.html"))
            if path.name != "field.html"
            and self._HAND_ROLLED_LABEL.search(path.read_text(encoding="utf-8"))
        ]

        self.assertEqual(
            offenders,
            [],
            "Поле формы собрано руками вместо ui/field.html: " + ", ".join(offenders),
        )


class FieldStateTests(TestCase):
    def test_rejected_field_is_marked_for_screen_readers(self):
        """Красная рамка — зрительный признак; скринридер её не видит."""
        response = Client().post(reverse("iam:login"), {"personnel_number": "", "password": ""})

        self.assertContains(response, 'aria-invalid="true"')

    def test_error_text_is_tied_to_its_field(self):
        response = Client().post(reverse("iam:login"), {"personnel_number": "", "password": ""})
        body = response.content.decode()

        described = re.search(r'aria-describedby="([^"]+)"', body)
        self.assertIsNotNone(described, "поле не связано с текстом ошибки")
        for anchor in described.group(1).split():
            self.assertIn(f'id="{anchor}"', body, f"ссылка на несуществующий {anchor}")

    def test_valid_field_is_not_marked_invalid(self):
        self.assertNotContains(Client().get(reverse("iam:login")), 'aria-invalid="true"')

    def test_required_field_marker_is_not_only_an_asterisk(self):
        """Звёздочка сама по себе не сообщает ничего тому, кто её не видит."""
        user = make_user(personnel_number="1000", role=User.Role.METHODIST)
        client = Client()
        client.login(personnel_number="1000", password=PASSWORD)

        response = client.get(reverse("documents:create"))

        self.assertContains(response, "обязательное поле")
        self.assertContains(response, 'aria-hidden="true"')

    def test_help_text_is_shown_and_tied_to_its_field(self):
        user = make_user(personnel_number="1001", role=User.Role.METHODIST)
        client = Client()
        client.login(personnel_number="1001", password=PASSWORD)
        document = make_document(reg_number="Ф-UI")

        body = client.get(reverse("documents:ocr_review", args=[document.pk])).content.decode()

        self.assertIn("-help", body)
        self.assertRegex(body, r'aria-describedby="[^"]*-help')

    def test_widget_keeps_its_own_class_when_aria_is_added(self):
        """as_widget(attrs=…) должен дополнять атрибуты виджета, а не затирать."""
        response = Client().post(reverse("iam:login"), {"personnel_number": "", "password": ""})
        body = response.content.decode()

        marked = [tag for tag in re.findall(r"<input[^>]*>", body) if 'aria-invalid="true"' in tag]
        self.assertTrue(marked, "нет ни одного отвергнутого поля")
        for tag in marked:
            self.assertIn('class="field"', tag, "потерян класс виджета: " + tag)
