"""
Доменная логика графа версионности, не сводимая к ограничениям на уровне
таблицы (ТЗ 4.2.2: «Алгоритм публикации проверяет граф связей на
ацикличность»).

CheckConstraint/UniqueConstraint на DocumentRelation (см. models.py)
ловят только самоссылку и точный дубль одной связи — по построению SQL
CHECK видит лишь вставляемую строку, а не весь граф. Цикл длиной больше
единицы (A → B → C → A) виден только обходом графа, поэтому это
рекурсивный запрос, а не ограничение таблицы.
"""
from django.apps import apps
from django.db import connection


def relation_would_create_cycle(from_document_id, to_document_id) -> bool:
    """True, если ребро from_document -> to_document создаст цикл — то
    есть to_document уже может достичь from_document по существующим связям."""
    if from_document_id == to_document_id:
        return True

    # Модель через apps.get_model(), а не прямой импорт — models.py вызывает
    # эту функцию из DocumentRelation.clean(), прямой импорт дал бы цикл.
    table = apps.get_model("documents", "DocumentRelation")._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH RECURSIVE reachable(id) AS (
                SELECT to_document_id FROM {table} WHERE from_document_id = %s
                UNION
                SELECT r.to_document_id
                FROM {table} r
                JOIN reachable ON r.from_document_id = reachable.id
            )
            SELECT 1 FROM reachable WHERE id = %s LIMIT 1
            """,
            [to_document_id, from_document_id],
        )
        return cursor.fetchone() is not None
