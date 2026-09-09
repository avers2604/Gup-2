from django.test import TestCase

from apps.core.storage import originals_storage
from apps.documents.retention import RetentionMode

from .models import Template


class TemplateStorageTests(TestCase):
    """Двухступенчатая модель (STACK.md, ревью retention): каждая строка
    Template — уже опубликованная версия (ТЗ 4.3.1 требует инкремента
    версии даже для минорной корректировки), поэтому её файлы должны
    лежать в заблокированном originals, а не в working."""

    def test_file_editable_uses_originals_storage(self):
        self.assertIs(Template._meta.get_field("file_editable").storage, originals_storage())

    def test_file_sample_uses_originals_storage(self):
        self.assertIs(Template._meta.get_field("file_sample").storage, originals_storage())

    def test_retention_mode_is_governance(self):
        # Governance, а не Compliance — по замыслу матрицы должен оставаться
        # снимаемым привилегированной ролью (замена файла — это НОВАЯ строка,
        # но исторические версии всё равно должны быть снимаемы при
        # обоснованной необходимости, в отличие от, например, приказов).
        self.assertEqual(Template.RETENTION_MODE, RetentionMode.GOVERNANCE)
