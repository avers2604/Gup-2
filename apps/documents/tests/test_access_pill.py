"""Гриф ДСП виден на каждом экране, где документ показан по имени.

Пробел нашёлся при первом живом просмотре интерфейса. Плашка «ДСП»
стояла в реестре и на карточке документа, а в результатах поиска и в
сводных редакциях — нет. Между тем документ с грифом попадает и туда,
и туда: фильтрация по допуску пропускает его тому, у кого допуск есть.

Последствие не косметическое. Пользователь с допуском видит в выдаче
поиска наименование ограниченного документа неотличимым от обычного —
и, скопировав его в письмо или в презентацию, узнаёт о грифе уже после.
Признак ограничения обязан ехать вместе с наименованием.
"""
from django.test import Client, TestCase
from django.urls import reverse

from apps.documents.models import DocumentRelation, NormativeDocument

from .factories import make_document
from .test_permissions import PASSWORD, make_user

#: Проверяется разметка плашки, а не слово «ДСП». Первая редакция теста
#: искала подстроку «ДСП» — и проходила без единой плашки, потому что
#: регистрационный номер документа-фикстуры был «ДСП-1». Тест на отсутствие
#: признака, который сам себе этот признак и подсовывает, бесполезен.
PILL = 'class="access-pill"'



class AccessPillIsShownEverywhereTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0800", dsp_access=True)
        self.client_ = Client()
        self.client_.login(personnel_number="0800", password=PASSWORD)

        self.restricted = make_document(
            reg_number="907-и",
            title="Инструкция по антитеррористической защищённости",
            status=NormativeDocument.Status.ACTIVE_AMENDED,
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        self.amendment = make_document(
            reg_number="908-и",
            title="Изменение к инструкции",
            status=NormativeDocument.Status.ACTIVE,
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        DocumentRelation.objects.create(
            from_document=self.amendment,
            to_document=self.restricted,
            relation_type=DocumentRelation.RelationType.AMENDS,
        )

    def test_registry_marks_the_restricted_document(self):
        self.assertContains(self.client_.get(reverse("documents:list")), PILL)

    def test_document_card_marks_the_restricted_document(self):
        response = self.client_.get(reverse("documents:detail", args=[self.restricted.pk]))
        self.assertContains(response, PILL)

    def test_consolidated_list_marks_the_restricted_document(self):
        response = self.client_.get(reverse("documents:consolidated_list"))
        self.assertContains(response, self.restricted.reg_number)
        self.assertContains(response, PILL)

    def test_consolidated_detail_marks_both_the_base_and_the_amendment(self):
        """Изменение — тоже документ, и гриф у него собственный."""
        response = self.client_.get(
            reverse("documents:consolidated_detail", args=[self.restricted.pk])
        )
        self.assertContains(response, self.amendment.reg_number)
        # Заголовок сводки и строка изменения — две отдельные плашки.
        self.assertGreaterEqual(response.content.decode().count(PILL), 2)

    def test_search_results_mark_the_restricted_document(self):
        """Самый дорогой из пропущенных экранов: из выдачи копируют текст."""
        response = self.client_.get(
            reverse("search_ocr:search"),
            {"q": self.restricted.title},
        )

        self.assertContains(response, self.restricted.reg_number)
        self.assertContains(response, PILL)

    def test_open_document_gets_no_pill(self):
        """Плашка — признак ограничения, а не украшение строки."""
        open_doc = make_document(
            reg_number="ОБЩИЙ-1", status=NormativeDocument.Status.ACTIVE
        )

        response = self.client_.get(reverse("documents:detail", args=[open_doc.pk]))

        self.assertNotContains(response, PILL)
