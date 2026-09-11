"""Регрессии конкурентного изменения графа версионности."""

import threading
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.documents import services
from apps.documents.models import DocumentRelation

from .factories import make_document


class ConcurrentRelationCycleTests(TransactionTestCase):
    """Параллельные рёбра не должны совместно превращать DAG в цикл."""

    def test_disjoint_concurrent_edges_cannot_jointly_close_cycle(self):
        # Уже есть B -> C и D -> A. По отдельности новые A -> B и C -> D
        # допустимы на исходном снимке, но вместе дают A -> B -> C -> D -> A.
        document_a = make_document(reg_number="RACE-A")
        document_b = make_document(reg_number="RACE-B")
        document_c = make_document(reg_number="RACE-C")
        document_d = make_document(reg_number="RACE-D")
        DocumentRelation.objects.create(
            from_document=document_b,
            to_document=document_c,
            relation_type=DocumentRelation.RelationType.AMENDS,
        )
        DocumentRelation.objects.create(
            from_document=document_d,
            to_document=document_a,
            relation_type=DocumentRelation.RelationType.AMENDS,
        )

        original_check = services.relation_would_create_cycle
        both_checked_old_graph = threading.Barrier(2)
        results = []
        result_lock = threading.Lock()

        def synchronized_check(from_document_id, to_document_id):
            result = original_check(from_document_id, to_document_id)
            if not result:
                try:
                    # На старом коде обе независимые транзакции доходят сюда
                    # одновременно и обе видят DAG до чужого INSERT. После
                    # исправления одна транзакция сериализуется до проверки;
                    # первая продолжит по timeout, вторая затем увидит уже
                    # закоммиченное ребро и вернёт True, не ожидая barrier.
                    both_checked_old_graph.wait(timeout=0.5)
                except threading.BrokenBarrierError:
                    pass
            return result

        def worker(from_id, to_id):
            close_old_connections()
            try:
                DocumentRelation.objects.create(
                    from_document_id=from_id,
                    to_document_id=to_id,
                    relation_type=DocumentRelation.RelationType.AMENDS,
                )
            except ValidationError:
                outcome = "validation_error"
            else:
                outcome = "ok"
            finally:
                connection.close()
            with result_lock:
                results.append(outcome)

        with patch(
            "apps.documents.services.relation_would_create_cycle",
            side_effect=synchronized_check,
        ):
            threads = [
                threading.Thread(target=worker, args=(document_a.pk, document_b.pk)),
                threading.Thread(target=worker, args=(document_c.pk, document_d.pk)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sorted(results), ["ok", "validation_error"])
        self.assertEqual(DocumentRelation.objects.count(), 3)
