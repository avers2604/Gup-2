from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from .models import BusinessMetricCounter, OcrReviewQueueEntry

logger = logging.getLogger(__name__)

SEARCH_REQUESTS = "search_requests"
SEARCH_ZERO_RESULTS = "search_zero_results"
LINK_FAILURE_PREFIX = "link_generation_failure_"
LINK_FAILURE_STATUSES = (403, 404, 504)
LOGIN_FAILURE_PURGE_SUCCESSES = "login_failure_purge_successes"
DEFAULT_SEARCH_METRIC_DEDUP_SECONDS = 30


def increment_counter(name: str, amount: int = 1) -> None:
    """Atomically increment a persisted counter and its last-mutation timestamp."""
    now = timezone.now()
    updated = BusinessMetricCounter.objects.filter(name=name).update(
        value=F("value") + amount,
        updated_at=now,
    )
    if updated:
        return
    try:
        # The savepoint is required when a caller already owns an outer
        # transaction (purge_login_failures does): a concurrent first INSERT
        # may violate the unique name constraint, but must not poison the
        # caller's whole transaction before the retry UPDATE.
        with transaction.atomic():
            BusinessMetricCounter.objects.create(name=name, value=amount)
    except IntegrityError:
        # Concurrent first writer won the INSERT. QuerySet.update() bypasses
        # auto_now, so updated_at must be advanced explicitly here as well.
        BusinessMetricCounter.objects.filter(name=name).update(
            value=F("value") + amount,
            updated_at=now,
        )


def build_search_metric_key(
    *,
    user_id,
    query: str,
    category=None,
    service=None,
    surface: str,
) -> str:
    """Build an unambiguous short-window identity for one logical search.

    Whitespace/case changes in the same query are normalized. Filters, user and
    delivery surface remain part of the identity so semantically different
    searches are never collapsed together.
    """
    normalized_query = " ".join(str(query or "").split()).casefold()
    payload = {
        "user_id": str(user_id),
        "query": normalized_query,
        "category": str(category or ""),
        "service": str(service or ""),
        "surface": str(surface or ""),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_search(
    result_count: int,
    *,
    page_number: int = 1,
    logical_search_key: str | None = None,
) -> None:
    """Record one logical search, excluding pagination and short-window repeats.

    Page 2..N is navigation through an already counted result set. Repeated
    first-page GETs with the same logical key (double-click, refresh, retry) are
    collapsed for a short configurable window using the shared Django cache.
    Production uses Redis, so the identity is consistent across app workers.
    """
    if page_number < 1:
        raise ValueError("page_number must be >= 1")
    if page_number != 1:
        return
    if not logical_search_key:
        raise ValueError("logical_search_key is required for first-page search metrics")

    timeout = int(
        getattr(settings, "SEARCH_METRIC_DEDUP_SECONDS", DEFAULT_SEARCH_METRIC_DEDUP_SECONDS)
    )
    if timeout <= 0:
        # Ошибка observability-конфига не должна превращать пользовательский
        # поиск в 500. Fail-open: считаем запрос без дедупликации и явно
        # сигнализируем конфигурационную проблему в журнале.
        logger.error(
            "SEARCH_METRIC_DEDUP_SECONDS must be > 0; counting search without dedupe"
        )
        is_new = True
    else:
        dedupe_key = f"bz-get:business-metric:search:{logical_search_key}"
        try:
            is_new = cache.add(dedupe_key, "1", timeout=timeout)
        except Exception:
            # Search availability must not depend on observability cache health.
            # Count the event rather than fail the user request; monitoring should
            # separately surface Redis/cache availability.
            logger.exception("Search metric dedupe cache is unavailable")
            is_new = True
    if not is_new:
        return

    increment_counter(SEARCH_REQUESTS)
    if result_count == 0:
        increment_counter(SEARCH_ZERO_RESULTS)


def record_link_generation_failure(status_code: int) -> None:
    """Record only failures raised while storage/link generation is attempted.

    Callers must not use this for business 404/409 states (unknown route,
    invisible/missing document, absent field, pending WORM promotion). Those are
    application semantics, not storage availability failures.
    """
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
    """Render business and reliability indicators required by Stage 4."""
    from django.db.models import Count, Min

    from apps.documents.models import NormativeDocument
    from apps.templates_bank.models import Template

    from .models import StagedFilePromotion, TaskOutbox

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

    purge_heartbeat = BusinessMetricCounter.objects.filter(
        name=LOGIN_FAILURE_PURGE_SUCCESSES
    ).values("value", "updated_at").first()
    purge_success_total = purge_heartbeat["value"] if purge_heartbeat else 0
    purge_last_success = int(purge_heartbeat["updated_at"].timestamp()) if purge_heartbeat else 0

    lines = [
        "# HELP bz_get_ocr_review_overdue_total Documents waiting for OCR manual review for more than 14 days.",
        "# TYPE bz_get_ocr_review_overdue_total gauge",
        f"bz_get_ocr_review_overdue_total {overdue_ocr}",
        "# HELP bz_get_templates_revision_overdue_total Active templates not reviewed for more than 3 years.",
        "# TYPE bz_get_templates_revision_overdue_total gauge",
        f"bz_get_templates_revision_overdue_total {overdue_templates}",
        "# HELP bz_get_search_requests_total Logical Web/API search executions; pagination and short-window repeats are excluded.",
        "# TYPE bz_get_search_requests_total counter",
        f"bz_get_search_requests_total {search_total}",
        "# HELP bz_get_search_zero_results_total Logical searches that returned zero documents after pagination/retry dedupe.",
        "# TYPE bz_get_search_zero_results_total counter",
        f"bz_get_search_zero_results_total {search_zero}",
        "# HELP bz_get_search_zero_result_ratio Lifetime ratio of deduplicated logical searches with zero results.",
        "# TYPE bz_get_search_zero_result_ratio gauge",
        f"bz_get_search_zero_result_ratio {zero_ratio:.8f}",
        "# HELP bz_get_link_generation_failures_total Storage/link-generation failures by HTTP status; business 404/409 states are excluded.",
        "# TYPE bz_get_link_generation_failures_total counter",
    ]
    for status in LINK_FAILURE_STATUSES:
        value = counters[f"{LINK_FAILURE_PREFIX}{status}"]
        lines.append(f'bz_get_link_generation_failures_total{{status="{status}"}} {value}')

    lines.extend([
        "# HELP bz_get_login_failure_purge_success_total Successful LoginFailure retention purge executions.",
        "# TYPE bz_get_login_failure_purge_success_total counter",
        f"bz_get_login_failure_purge_success_total {purge_success_total}",
        "# HELP bz_get_login_failure_purge_last_success_unixtime Unix timestamp of the last successful LoginFailure retention purge; 0 means never observed.",
        "# TYPE bz_get_login_failure_purge_last_success_unixtime gauge",
        f"bz_get_login_failure_purge_last_success_unixtime {purge_last_success}",
    ])

    pending = TaskOutbox.objects.filter(delivered_at__isnull=True).aggregate(
        count=Count("pk"), oldest=Min("created_at")
    )
    age = max(0.0, (now - pending["oldest"]).total_seconds()) if pending["oldest"] else 0.0
    lines.extend([
        "# HELP bz_get_outbox_pending Pending broker delivery intents.",
        "# TYPE bz_get_outbox_pending gauge",
        f"bz_get_outbox_pending {pending['count']}",
        "# HELP bz_get_outbox_oldest_seconds Age of oldest pending broker delivery intent.",
        "# TYPE bz_get_outbox_oldest_seconds gauge",
        f"bz_get_outbox_oldest_seconds {age:.3f}",
    ])

    promotions = StagedFilePromotion.objects.filter(completed_at__isnull=True).aggregate(
        count=Count("pk"), oldest=Min("created_at")
    )
    promotion_age = (
        max(0.0, (now - promotions["oldest"]).total_seconds())
        if promotions["oldest"] else 0.0
    )
    lines.extend([
        "# HELP bz_get_worm_promotions_pending Files committed in DB but not yet verified in Object-Locked storage.",
        "# TYPE bz_get_worm_promotions_pending gauge",
        f"bz_get_worm_promotions_pending {promotions['count']}",
        "# HELP bz_get_worm_promotion_oldest_seconds Age of the oldest unfinished WORM promotion.",
        "# TYPE bz_get_worm_promotion_oldest_seconds gauge",
        f"bz_get_worm_promotion_oldest_seconds {promotion_age:.3f}",
    ])
    return "\n".join(lines) + "\n"
