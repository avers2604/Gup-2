"""Регрессии конкурентного снятия связей версионности."""

import threading
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.audit.models import AuditLog
from apps.documents import services
from apps.documents.models import DocumentRelation
from apps.iam.models import User

from .factories import make_document
from .test_permissions import make_user


class ConcurrentRelationRemovalTests(TransactionTestCase):
    """Одно фактическое снятие связи должно давать одно WORM-событие."""

    def test_concurrent_removal_is_audited_exactly_once(self):
        actor = make_user(personnel_number="0730", role=User.Role.METHODIST)
        source = make_document(reg_number="REMOVE-RACE-A")
        target = make_document(reg_number="REMOVE-RACE-B")
        relation = DocumentRelation.objects.create(
            from_document=source,
            to_document=target,
            relation_type=DocumentRelation.RelationType.REFERENCES,
        )

        original_log = services._log_relation_event
        both_logged = threading.Barrier(2)
        start = threading.Barrier(2)
        outcomes = []
        outcome_lock = threading.Lock()

        def synchronized_log(*, actor, relation, event_name):
            original_log(actor=actor, relation=relation, event_name=event_name)
            if event_name == "DOCUMENT_RELATION_REMOVED":
                try:
                    # На старом коде обе транзакции успевают зафиксировать
                    # неизменяемое событие ДО того, как одна из них реально
                    # удалит строку. После исправления вторая транзакция
                    # блокируется на select_for_update и сюда уже не доходит.
                    both_logged.wait(timeout=0.5)
                except threading.BrokenBarrierError:
                    pass

        def worker():
            close_old_connections()
            try:
                stale_relation = (
                    DocumentRelation.objects.select_related("from_document", "to_document")
                    .get(pk=relation.pk)
                )
                start.wait(timeout=2)
                result = services.remove_relation(actor=actor, relation=stale_relation)
                outcome = "removed" if result else "already_absent"
            except Exception as exc:  # captured so the main thread can assert deterministically
                outcome = type(exc).__name__
            finally:
                connection.close()
            with outcome_lock:
                outcomes.append(outcome)

        with patch("apps.documents.services._log_relation_event", side_effect=synchronized_log):
            threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertFalse(DocumentRelation.objects.filter(pk=relation.pk).exists())
        self.assertEqual(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.DOCUMENT_RELATION_REMOVED,
                object_id=str(source.pk),
            ).count(),
            1,
        )
        self.assertEqual(sorted(outcomes), ["already_absent", "removed"])
