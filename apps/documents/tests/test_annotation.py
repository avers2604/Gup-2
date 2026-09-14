"""Автосборка аннотации: что она умеет и где честно сдаётся.

Сборщик извлекающий, а не порождающий (см. apps/documents/annotation.py),
и тесты написаны под это: они проверяют, что из документа, оформленного
по правилам, берётся цель и существо, а из мусора не берётся ничего.
Проверять «качество пересказа» здесь нечего — пересказа не существует.
"""
from django.test import SimpleTestCase, TestCase

from apps.documents.annotation import MAX_LENGTH, build_annotation
from apps.documents.models import NormativeDocument

from .factories import make_document

ORDER = """СПб ГУП «Горэлектротранс»
ПРИКАЗ
15.01.2024                № 301-п
г. Санкт-Петербург
О внесении изменений в инструкцию по осмотру пути

В целях повышения безопасности движения трамвайного транспорта и приведения
локальных актов в соответствие с требованиями Правил технической эксплуатации,
ПРИКАЗЫВАЮ:
1. Утвердить Инструкцию по осмотру трамвайного пути в новой редакции согласно
приложению к настоящему приказу.
2. Начальникам служб обеспечить ознакомление работников под подпись.
3. Контроль за исполнением настоящего приказа оставляю за собой.
"""

REGULATION = """ПОЛОЖЕНИЕ
о системе управления охраной труда
1. Общие положения
1.1. Настоящее Положение устанавливает порядок функционирования системы
управления охраной труда в СПб ГУП «Горэлектротранс».
1.2. Положение разработано в соответствии с Трудовым кодексом.
"""


class OrderParsingTests(SimpleTestCase):
    def test_purpose_and_operative_clause_are_taken(self):
        annotation = build_annotation(ORDER)

        self.assertIn("В целях повышения безопасности", annotation)
        self.assertIn("Утвердить Инструкцию по осмотру", annotation)

    def test_service_header_is_dropped(self):
        """Реквизиты — не содержание: они есть в самой карточке."""
        annotation = build_annotation(ORDER)

        self.assertNotIn("Горэлектротранс»", annotation)
        self.assertNotIn("301-п", annotation)
        self.assertNotIn("Санкт-Петербург", annotation)

    def test_boilerplate_clause_is_dropped(self):
        """«Контроль за исполнением оставляю за собой» есть в каждом
        втором приказе и не отличает один документ от другого ничем."""
        self.assertNotIn("Контроль за исполнением", build_annotation(ORDER))

    def test_clause_broken_across_lines_is_not_cut_at_the_line_break(self):
        """Распознанный скан ломает строки по ширине листа, а не по смыслу."""
        self.assertIn("приложению к настоящему приказу", build_annotation(ORDER))

    def test_parts_are_joined_into_readable_sentences(self):
        """Целевая часть обрывается там, где стояло «ПРИКАЗЫВАЮ», то есть
        без знака в конце — без точки две фразы слипаются в одну."""
        self.assertNotIn("эксплуатации Утвердить", build_annotation(ORDER))


class RegulationParsingTests(SimpleTestCase):
    def test_self_definition_is_taken(self):
        annotation = build_annotation(REGULATION)

        self.assertIn("устанавливает порядок функционирования", annotation)

    def test_clause_numbering_is_stripped(self):
        self.assertFalse(build_annotation(REGULATION).startswith("1."))


class GivesUpHonestlyTests(SimpleTestCase):
    """Пустая строка лучше выдуманной аннотации: собранную из мусора
    прочитают и поверят."""

    def test_empty_text(self):
        self.assertEqual(build_annotation(""), "")

    def test_whitespace_only(self):
        self.assertEqual(build_annotation("   \n\n\t"), "")

    def test_header_only(self):
        self.assertEqual(build_annotation("ПРИКАЗ\n№ 12\n15.01.2024\n"), "")

    def test_fragments_shorter_than_a_sentence(self):
        self.assertEqual(build_annotation("Да.\nНет.\n1.\n2.\n"), "")


class FreeFormTextTests(SimpleTestCase):
    def test_falls_back_to_first_sentences(self):
        text = (
            "Уважаемые коллеги, доводим до сведения, что с 1 марта меняется\n"
            "порядок выдачи путевых листов в депо номер три.\n"
            "Обращайтесь к диспетчеру парка за разъяснениями.\n"
        )

        annotation = build_annotation(text)

        self.assertIn("меняется порядок выдачи путевых листов", annotation)
        self.assertNotIn("порядок. выдачи", annotation)


class LengthTests(SimpleTestCase):
    def test_long_annotation_is_trimmed_at_a_sentence_boundary(self):
        clause = "Утвердить порядок взаимодействия подразделений предприятия. "
        text = "ПРИКАЗЫВАЮ:\n1. " + clause * 20

        annotation = build_annotation(text)

        self.assertLessEqual(len(annotation), MAX_LENGTH)
        self.assertTrue(
            annotation.endswith((".", "…")),
            f"аннотация оборвана на полуслове: …{annotation[-40:]!r}",
        )


class DoesNotOverwriteHumanTextTests(TestCase):
    """Главная граница функции.

    Аннотация, написанная методистом, — его работа. Конвейер OCR
    отрабатывает при каждой замене файла, и если бы он её перезаписывал,
    выверенный текст исчезал бы молча, без следа и без возможности
    восстановить.
    """

    def _run_pipeline(self, document, text):
        """Та же ветка, что и в apps/documents/tasks.py."""
        from apps.documents.annotation import build_annotation as build

        if not document.summary or document.summary_is_auto:
            annotation = build(text)
            if annotation:
                document.summary = annotation
                document.summary_is_auto = True
                document.save(update_fields=["summary", "summary_is_auto"])
        return document

    def test_empty_annotation_is_filled(self):
        document = make_document(reg_number="400-п", summary="")

        self._run_pipeline(document, ORDER)

        self.assertTrue(document.summary)
        self.assertTrue(document.summary_is_auto)

    def test_human_written_annotation_survives(self):
        document = make_document(reg_number="401-п", summary="Выверено методистом.")

        self._run_pipeline(document, ORDER)

        self.assertEqual(document.summary, "Выверено методистом.")
        self.assertFalse(document.summary_is_auto)

    def test_previously_auto_annotation_is_refreshed(self):
        """Текст скана мог измениться вместе с файлом — своя же прошлая
        сборка не является чьей-то работой и обновляется."""
        document = make_document(reg_number="402-п", summary="Старая сборка.")
        NormativeDocument.objects.filter(pk=document.pk).update(summary_is_auto=True)
        document.refresh_from_db()

        self._run_pipeline(document, ORDER)

        self.assertNotEqual(document.summary, "Старая сборка.")
        self.assertIn("В целях повышения безопасности", document.summary)
