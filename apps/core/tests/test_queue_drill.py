from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.core.models import QueueDrillProbe
from apps.core.tasks import queue_acceptance_probe


class QueueAcceptanceProbeTests(TestCase):
    def test_duplicate_delivery_has_one_durable_completion(self):
        probe = QueueDrillProbe.objects.create(run_id="ci-worker", sequence=1)

        queue_acceptance_probe.apply(args=[probe.pk, 0]).get()
        queue_acceptance_probe.apply(args=[probe.pk, 0]).get()

        probe.refresh_from_db()
        self.assertEqual(probe.delivery_count, 2)
        self.assertEqual(probe.completion_count, 1)
        self.assertIsNotNone(probe.completed_at)

    def test_task_is_configured_for_worker_loss_redelivery(self):
        self.assertTrue(queue_acceptance_probe.acks_late)
        self.assertTrue(queue_acceptance_probe.reject_on_worker_lost)
        self.assertTrue(queue_acceptance_probe.track_started)

    def test_verify_accepts_redelivery_without_duplicate_business_effect(self):
        completed_at = timezone.now()
        QueueDrillProbe.objects.create(
            run_id="ci-verify",
            sequence=1,
            delivery_count=2,
            completion_count=1,
            completed_at=completed_at,
        )
        QueueDrillProbe.objects.create(
            run_id="ci-verify",
            sequence=2,
            delivery_count=1,
            completion_count=1,
            completed_at=completed_at,
        )

        call_command("queue_drill", "verify", "--run-id", "ci-verify", "--expected-count", "2")
