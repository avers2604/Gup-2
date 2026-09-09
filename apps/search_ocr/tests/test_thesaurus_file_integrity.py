"""
Регрессионный страж целостности docs/thesaurus/thesaurus_v0.9_draft.json —
файл продолжит редактироваться командой (курируется службами, ТЗ 4.4.1),
эти тесты ловят структурные ошибки (невалидный JSON, дубль id, код
категории/службы вне справочника) до того, как они попадут в импорт на
проде, а не полагаются на то, что кто-то заметит их вручную в diff'е."""
import json
from pathlib import Path

from django.test import SimpleTestCase

from ..models import ThesaurusCategory, ThesaurusService

THESAURUS_PATH = Path(__file__).resolve().parents[3] / "docs" / "thesaurus" / "thesaurus_v0.9_draft.json"


class ThesaurusFileIntegrityTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data = json.loads(THESAURUS_PATH.read_text(encoding="utf-8"))
        cls.entries = cls.data["entries"]

    def test_file_exists_and_parses(self):
        self.assertTrue(THESAURUS_PATH.exists())
        self.assertIsInstance(self.entries, list)
        self.assertGreater(len(self.entries), 0)

    def test_no_duplicate_ids(self):
        ids = [e["id"] for e in self.entries]
        self.assertEqual(len(ids), len(set(ids)), "В файле есть повторяющиеся id.")

    def test_th08_at_least_150_entries(self):
        self.assertGreaterEqual(len(self.entries), 150)

    def test_all_ids_are_valid_slugs(self):
        # SlugField (ASCII) — та же проверка, что и в ThesaurusEntry.id;
        # здесь ловим типографские опечатки (смешение кириллицы/латиницы
        # внутри одного id) раньше, чем их поймает import_thesaurus.
        import re

        slug_re = re.compile(r"^[-a-zA-Z0-9_]+$")
        bad = [e["id"] for e in self.entries if not slug_re.match(e["id"])]
        self.assertEqual(bad, [], f"id не являются валидными slug (ASCII): {bad}")

    def test_all_categories_are_known(self):
        valid = set(ThesaurusCategory.values)
        bad = sorted({e["category"] for e in self.entries} - valid)
        self.assertEqual(bad, [], f"Категории вне справочника meta.categories: {bad}")

    def test_all_services_are_known_or_null(self):
        valid = set(ThesaurusService.values)
        bad = sorted({e["service"] for e in self.entries if e.get("service") is not None} - valid)
        self.assertEqual(bad, [], f"Службы вне справочника meta.services: {bad}")

    def test_python_categories_match_json_meta_categories(self):
        # Защита от дрейфа: ThesaurusCategory продублирован в Python (см.
        # docstring в models.py) — если файл получит новую категорию без
        # обновления Python-класса, этот тест упадёт первым.
        json_categories = set(self.data["meta"]["categories"].keys())
        python_categories = set(ThesaurusCategory.values)
        self.assertEqual(json_categories, python_categories)

    def test_python_services_match_json_meta_services(self):
        json_services = {k for k in self.data["meta"]["services"].keys() if k != "null"}
        python_services = set(ThesaurusService.values)
        self.assertEqual(json_services, python_services)

    def test_th04_every_entry_has_short_form_or_synonym(self):
        bad = [
            e["id"] for e in self.entries
            if not e.get("short_forms") and not e.get("synonyms")
        ]
        self.assertEqual(bad, [], f"Записи без short_forms и synonyms (TH-04): {bad}")

    def test_th01_canonical_unique_case_insensitively(self):
        seen: dict[str, str] = {}
        collisions = []
        for e in self.entries:
            norm = e["canonical"].strip().lower()
            if norm in seen:
                collisions.append((seen[norm], e["id"]))
            else:
                seen[norm] = e["id"]
        self.assertEqual(collisions, [], f"Дубли canonical без учёта регистра (TH-01): {collisions}")

    def test_th06_weight_in_range(self):
        bad = [e["id"] for e in self.entries if not (0.0 <= e.get("weight", 1.0) <= 1.0)]
        self.assertEqual(bad, [], f"weight вне диапазона [0.0, 1.0] (TH-06): {bad}")

    def test_ambiguity_registry_candidates_reference_real_ids(self):
        # candidate.id = null — намеренный маркер "не является записью
        # тезауруса" (например, вольт-ампер как единица измерения) с
        # обязательным reason; проверяем только НЕ-null кандидатов.
        real_ids = {e["id"] for e in self.entries}
        bad = []
        for group in self.data.get("ambiguity_registry", []):
            for candidate in group.get("candidates", []):
                cid = candidate["id"]
                if cid is not None and cid not in real_ids:
                    bad.append((group["abbr"], cid))
        self.assertEqual(bad, [], f"ambiguity_registry ссылается на несуществующие id: {bad}")

    def test_ambiguity_registry_null_candidates_have_a_reason(self):
        bad = []
        for group in self.data.get("ambiguity_registry", []):
            for candidate in group.get("candidates", []):
                if candidate["id"] is None and not candidate.get("reason", "").strip():
                    bad.append(group["abbr"])
        self.assertEqual(bad, [], f"candidate.id=null без обоснования (reason): {bad}")
