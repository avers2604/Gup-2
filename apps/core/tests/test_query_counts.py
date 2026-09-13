"""Число запросов к БД не зависит от числа строк на экране.

Экраны АИС листают реестр НРД, журнал аудита и банк бланков — списки,
которые у Заказчика вырастут до тысяч записей. N+1 в таком списке не
падает и не ломает вёрстку: страница просто отвечает секунду вместо
двадцати миллисекунд, и заметно это становится не в разработке, а на
проде под нагрузкой.

Тест устроен как сравнение, а не как assertNumQueries(8). Точное число
запросов — величина, законно меняющаяся от любой правки вьюхи: добавили
проверку прав, убрали лишний count — и тест падает, не найдя ни одного
дефекта. Инвариант, который здесь действительно нужен, другой:
**сколько бы строк ни было на странице, запросов столько же**. Он ловит
ровно N+1 и молчит на всём остальном.

Порог роста — 0. Не «немного», не «в разумных пределах»: любой запрос,
зависящий от числа строк, — это и есть дефект, ради которого тест
написан.
"""
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.documents.models import DocumentRelation, NormativeDocument
from apps.documents.tests.factories import make_document
from apps.documents.tests.test_permissions import PASSWORD, make_user  # noqa: F401
from apps.iam.models import User

#: Две точки замера. Двадцать пять строк — заведомо больше единицы и
#: заведомо меньше страницы (PAGE_SIZE реестра), так что пагинация
#: одинаково не вмешивается ни в один из замеров.
FEW = 1
MANY = 25

def seed(n, start=0):
    """n документов «Действует с изм.», у каждого по три изменения,
    плюс один документ, у которого изменений n штук.

    Оба измерения нужны: первое растит длину списка, второе — глубину
    одной сводки. N+1 может сидеть и там, и там, и это разные дефекты.
    """
    for i in range(start, start + n):
        base = make_document(
            reg_number=f"{100 + i}-п", status=NormativeDocument.Status.ACTIVE_AMENDED
        )
        for j in range(3):
            DocumentRelation.objects.create(
                from_document=make_document(
                    reg_number=f"{100 + i}-изм-{j}", status=NormativeDocument.Status.ACTIVE
                ),
                to_document=base,
                relation_type=DocumentRelation.RelationType.AMENDS,
            )

    deep = NormativeDocument.objects.filter(reg_number="СВОД-1").first() or make_document(
        reg_number="СВОД-1", status=NormativeDocument.Status.ACTIVE_AMENDED
    )
    for j in range(start, start + n):
        DocumentRelation.objects.create(
            from_document=make_document(
                reg_number=f"СВОД-1-изм-{j}", status=NormativeDocument.Status.ACTIVE
            ),
            to_document=deep,
            relation_type=DocumentRelation.RelationType.AMENDS,
        )

    for _ in range(n * 2):
        AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_REVOKED,
            actor_personnel_number="0900",
            object_type="NormativeDocument",
            object_id=str(deep.pk),
            details={"reg_number": deep.reg_number},
        )
    return deep


class ScreenQueryCountsDoNotGrowWithRowsTests(TestCase):
    def _count(self, client, url, params=None):
        with CaptureQueriesContext(connection) as captured:
            response = client.get(url, params or {})
        self.assertEqual(response.status_code, 200, f"{url} ответил {response.status_code}")
        return len(captured)

    def _warm_up(self, client):
        """Один холостой прогон до замеров.

        Строка счётчика бизнес-метрики (core_businessmetriccounter)
        создаётся при самом первом поиске: UPDATE не находит строки, и
        Django делает SAVEPOINT + INSERT + RELEASE — три лишних запроса.
        Это разовый upsert, а не зависимость от числа строк; без прогрева
        тест «находил» отрицательный N+1 на поиске (12 → 9).
        """
        client.get(reverse("search_ocr:search"), {"q": "прогрев"})

    def _counts(self, client, deep):
        # Повторный тот же поиск стоит на четыре запроса дешевле: бизнес-
        # метрика (apps/core/business_metrics.record_search) схлопывает
        # одинаковые запросы в окне дедупликации через общий кеш. Это не
        # экономия вьюхи, а артефакт того, что замер делается дважды
        # подряд, — без сброса кеша тест «находил» отрицательный N+1.
        cache.clear()
        return {
            "реестр НРД": self._count(client, reverse("documents:list")),
            "карточка НРД": self._count(
                client, reverse("documents:detail", args=[deep.pk])
            ),
            "сводные редакции": self._count(client, reverse("documents:consolidated_list")),
            "сводка по документу": self._count(
                client, reverse("documents:consolidated_detail", args=[deep.pk])
            ),
            "журнал аудита": self._count(client, reverse("audit:list")),
            "поиск": self._count(
                client, reverse("search_ocr:search"), {"q": "Тестовый документ"}
            ),
            "банк бланков": self._count(client, reverse("templates_bank:family_list")),
        }

    def test_query_count_is_the_same_with_one_row_and_with_twenty_five(self):
        # Офицер ИБ с допуском ДСП — единственная роль, которой видны все
        # семь экранов сразу; иначе журнал пришлось бы мерить отдельно.
        user = make_user(
            personnel_number="0900", role=User.Role.SECURITY_OFFICER, dsp_access=True
        )
        client = Client()
        client.force_login(user)

        # Оба замера — в одном методе и в одной транзакции: строки только
        # доливаются. Разнести их по двум тестам не выйдет — AuditLog WORM
        # и delete() запрещает, так что журнал между замерами не очистить.
        deep = seed(FEW)
        self._warm_up(client)
        few = self._counts(client, deep)

        seed(MANY - FEW, start=FEW)
        self.assertEqual(
            NormativeDocument.objects.filter(status=NormativeDocument.Status.ACTIVE_AMENDED)
            .count(),
            MANY + 1,
            "замер бессмысленен, если строк не прибавилось",
        )
        many = self._counts(client, deep)

        growing = {
            screen: (few[screen], many[screen])
            for screen in few
            if many[screen] != few[screen]
        }

        self.assertEqual(
            growing,
            {},
            "Число запросов выросло вместе с числом строк — это N+1: "
            + "; ".join(
                f"{screen}: {before} → {after} при {FEW} → {MANY} строк"
                for screen, (before, after) in sorted(growing.items())
            ),
        )
