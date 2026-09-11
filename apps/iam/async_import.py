from __future__ import annotations

import uuid

from django.core.files import File

from apps.core.storage import working_storage

from .models import User
from .tasks import run_personnel_import_task


def stage_personnel_import(file_obj, *, original_name: str = "personnel.xlsx") -> str:
    """Persist an upload in shared working storage and return its object name."""
    suffix = ".xlsx" if original_name.lower().endswith(".xlsx") else ""
    object_name = f"personnel-imports/{uuid.uuid4().hex}{suffix}"
    storage = working_storage()
    if isinstance(file_obj, File):
        django_file = file_obj
    else:
        django_file = File(file_obj, name=original_name)
    return storage.save(object_name, django_file)


def enqueue_personnel_import(file_obj, *, actor: User | None = None, original_name: str = "personnel.xlsx") -> str:
    """Stage an Excel file and enqueue its import. Returns the Celery task id."""
    storage_name = stage_personnel_import(file_obj, original_name=original_name)
    task = run_personnel_import_task.delay(
        storage_name=storage_name,
        actor_id=str(actor.pk) if actor else None,
    )
    return task.id
