from __future__ import annotations

import uuid

from django.core.files import File

from apps.core import antivirus, upload_validation
from apps.core.outbox import enqueue
from apps.core.storage import working_storage

from .models import User


PERSONNEL_IMPORT_TASK = "apps.iam.tasks.run_personnel_import_task"


def _validated_personnel_upload(file_obj, *, original_name: str) -> File:
    """Validate an XLSX personnel import before any shared-storage write."""
    if not original_name.lower().endswith(".xlsx"):
        raise upload_validation.InvalidOOXML(
            "Файл импорта персонала должен иметь формат XLSX."
        )
    if isinstance(file_obj, File):
        django_file = file_obj
    else:
        django_file = File(file_obj, name=original_name)

    upload_validation.validate_upload_size(django_file)
    upload_validation.validate_ooxml(django_file, "xlsx")
    antivirus.scan_file(django_file)
    django_file.seek(0)
    return django_file


def stage_personnel_import(file_obj, *, original_name: str = "personnel.xlsx") -> str:
    """Validate and persist an XLSX upload in shared working storage."""
    django_file = _validated_personnel_upload(file_obj, original_name=original_name)
    object_name = f"personnel-imports/{uuid.uuid4().hex}.xlsx"
    storage = working_storage()
    return storage.save(object_name, django_file)


def enqueue_personnel_import(file_obj, *, actor: User | None = None, original_name: str = "personnel.xlsx") -> str:
    """Stage a validated Excel file and durably enqueue its import.

    Возвращаем UUID TaskOutbox: dispatcher использует его же как Celery task_id,
    поэтому внешний контракт management-команды `--async` сохраняется — этим
    идентификатором можно опрашивать AsyncResult после фактической доставки.
    Отказ брокера после staging не теряет импорт: outbox остаётся в PostgreSQL
    и будет повторно доставлен через dispatch_pending().
    """
    storage_name = stage_personnel_import(file_obj, original_name=original_name)
    entry = enqueue(
        PERSONNEL_IMPORT_TASK,
        [storage_name, str(actor.pk) if actor else None],
    )
    return str(entry.pk)
