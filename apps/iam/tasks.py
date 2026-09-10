from __future__ import annotations

from celery import shared_task

from apps.core.storage import working_storage

from .models import User
from .services import import_personnel


def _row_to_dict(row):
    return {
        "row_number": row.row_number,
        "tab_number": row.tab_number,
        "detail": row.detail,
    }


@shared_task(bind=True, autoretry_for=(OSError,), retry_backoff=True, max_retries=2)
def run_personnel_import_task(self, *, storage_name: str, actor_id: str | None = None) -> dict:
    """Process one staged Excel import and return a JSON-serializable report."""
    storage = working_storage()
    actor = User.objects.filter(pk=actor_id).first() if actor_id else None
    try:
        with storage.open(storage_name, "rb") as stream:
            report = import_personnel(stream, actor=actor)
        return {
            "total": report.total,
            "created": len(report.successes),
            "updated": len(report.updates),
            "errors": len(report.errors),
            "error_rows": [_row_to_dict(row) for row in report.errors],
        }
    finally:
        # The staged source is temporary. Result metadata lives in the Celery
        # backend and source cleanup must happen even when validation fails.
        if storage.exists(storage_name):
            storage.delete(storage_name)
