from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.documents.models import NormativeDocument

from .tasks import refresh_document_search_index_task


@receiver(post_save, sender=NormativeDocument, dispatch_uid="search_ocr_refresh_document_index")
def schedule_document_index_refresh(sender, instance, **kwargs):
    document_id = str(instance.pk)
    transaction.on_commit(lambda: refresh_document_search_index_task.delay(document_id))
