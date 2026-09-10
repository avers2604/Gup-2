from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError
from django.db.models import F, Q
from django.utils import timezone

from .models import BusinessMetricCounter, OcrReviewQueueEntry

SEARCH_REQUESTS = "search_requests"
SEARCH_ZERO_RESULTS = "search_zero_results"
LINK_FAILURE_PREFIX = "link_generation_failure_"
LINK_FAILURE_STATUSES = (403, 404, 504)


def increment_counter(name: str, amount: int = 1) -> None:
    """Atomically increment a persisted counter without process-local state."""
    updated = BusinessMetricCounter.objects.filter(name=name).update(value=F("value") + amount)
    if updated:
        return
    try:
        BusinessMetricCounter.objects.create(name=name, value=amount)
    except IntegrityError:
        BusinessMetricCounter.objects.filter(name=name).update(value=F("value") + amount)


def record_search(result_count: int) -> None:
    increment_counter(SEARCH_REQUESTS)
    if result_count == 0:
        increment_counter(SEARCH_ZERO_RESULTS)


def record_link_generation_failure(status_code: int) -> None:
    if status_code not in LINK_FAILURE_STATUSES:
        raise ValueError(f"unsupported link failure status: {status_code}")
    increment_counter(f"{LINK_FAILURE_PREFIX}{status_code}")


def sync_ocr_review_queue(document_id, *, needs_review: bool) -> None:
    """Keep a stable timestamp for the moment manual OCR review became required."""
    if needs_review:
        OcrReviewQueueEntry.objects.get_or_create(
            document_id=document_id,
            defaults={"required_at": timezone.now()},
        )
    else:
        OcrReviewQueueEntry.objects.filter(document_id=document_id).delete()


def _counter_values() -> dict[str, int]:
    names = [SEARCH_REQUESTS, SEARCH_ZERO_RESULTS] + [
        f"{LINK_FAILURE_PREFIX}{status}" for status in LINK_FAILURE_STATUSES
    ]
    values = {name: 0 for name in names}
    values.update(dict(BusinessMetricCounter.objects.filter(name__in=names).values_list("name", "value")))
    return values


def render_prometheus() -> str:
    """Render the four business indicators required by Stage 4 acceptance."""
    from apps.documents.models import NormativeDocument
    from apps.templates_bank.models import Template

    now = timezone.now()
    ocr_cutoff = now - timedelta(days=14)
    template_cutoff_date = (now - timedelta(days=365 * 3)).date()
    template_cutoff_dt = now - timedelta(days=365 * 3)

    active_review_ids = NormativeDocument.objects.filter(
        ocr_status=NormativeDocument.OcrStatus.NEEDS_REVIEW
    ).values("pk")
    overdue_ocr = OcrReviewQueueEntry.objects.filter(
        required_at__lt=ocr_cutoff,
        document_id__in=active_review_ids,
    ).count()
    overdue_templates = Template.objects.filter(status=Template.Status.ACTIVE).filter(
        Q(last_reviewed_at__lt=template_cutoff_date)
        | Q(last_reviewed_at__isnull=True, created_at__lt=template_cutoff_dt)
    ).count()

    counters = _counter_values()
    search_total = counters[SEARCH_REQUESTS]
    search_zero = counters[SEARCH_ZERO_RESULTS]
    zero_ratio = (search_zero / search_total) if search_total else 0.0

    lines = [
        "# HELP bz_get_ocr_review_overdue_total Documents waiting for OCR manual review for more than 14 days.",
        "# TYPE bz_get_ocr_review_overdue_total gauge",
        f"bz_get_ocr_review_overdue_total {overdue_ocr}",
        "# HELP bz_get_templates_revision_overdue_total Active templates not reviewed for more than 3 years.",
        "# TYPE bz_get_templates_revision_overdue_total gauge",
        f"bz_get_templates_revision_overdue_total {overdue_templates}",
        "# HELP bz_get_search_requests_total Valid search requests executed by Web/API.",
        "# TYPE bz_get_search_requests_total counter",
        f"bz_get_search_requests_total {search_total}",
        "# HELP bz_get_search_zero_results_total Valid search requests that returned zero documents.",
        "# TYPE bz_get_search_zero_results_total counter",
        f"bz_get_search_zero_results_total {search_zero}",
        "# HELP bz_get_search_zero_result_ratio Lifetime ratio of valid search requests with zero results.",
        "# TYPE bz_get_search_zero_result_ratio gauge",
        f"bz_get_search_zero_result_ratio {zero_ratio:.8f}",
        "# HELP bz_get_link_generation_failures_total Failed document-link generation attempts by HTTP status.",
        "# TYPE bz_get_link_generation_failures_total counter",
    ]
    for status in LINK_FAILURE_STATUSES:
        value = counters[f"{LINK_FAILURE_PREFIX}{status}"]
        lines.append(f'bz_get_link_generation_failures_total{{status="{status}"}} {value}')
    from django.db.models import Count, Min
    from .models import TaskOutbox
    pending = TaskOutbox.objects.filter(delivered_at__isnull=True).aggregate(count=Count("pk"), oldest=Min("created_at"))
    age = max(0.0, (timezone.now() - pending["oldest"]).total_seconds()) if pending["oldest"] else 0.0
    lines.extend([
        "# HELP bz_get_outbox_pending Pending broker delivery intents.",
        "# TYPE bz_get_outbox_pending gauge",
        f"bz_get_outbox_pending {pending['count']}",
        "# HELP bz_get_outbox_oldest_seconds Age of oldest pending broker delivery intent.",
        "# TYPE bz_get_outbox_oldest_seconds gauge",
        f"bz_get_outbox_oldest_seconds {age:.3f}",
    ])
    return "\n".join(lines) + "\n"
