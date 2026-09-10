from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.documents.models import NormativeDocument

from .indexing import refresh_document_search_index
from .tasks import refresh_document_search_index_task


@receiver(post_save, sender=NormativeDocument, dispatch_uid="search_ocr_refresh_document_index")
def schedule_document_index_refresh(sender, instance, **kwargs):
    document_id = str(instance.pk)
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        # Django TestCase keeps an outer transaction open, so on_commit would
        # run only after assertions. Eager mode intentionally keeps the read
        # model consistent in the same connection.
        refresh_document_search_index(document_id)
        return
    transaction.on_commit(lambda: refresh_document_search_index_task.delay(document_id))
