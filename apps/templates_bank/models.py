import uuid

from django.db import models

from apps.core.models import TimeStampedModel
from apps.core.storage import working_storage
from apps.documents.models import NormativeDocument


class TemplateFamily(models.Model):
    """Единый GUID семейства форм — lineage_root_id из ТЗ 4.3.1
    (например, «Акт схода подвижного состава» вне зависимости от редакции)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, verbose_name="Наименование семейства форм")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Семейство форм"
        verbose_name_plural = "Семейства форм"

    def __str__(self):
        return self.name


class Template(TimeStampedModel):
    """Версия бланка внутри семейства — правила переиздания ТЗ 4.3.1."""

    class ChangeType(models.TextChoices):
        MAJOR = "major", "Новая редакция"
        MINOR = "minor", "Корректировка"

    class Status(models.TextChoices):
        ACTIVE = "active", "Активна"
        SUPERSEDED = "superseded", "superseded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    family = models.ForeignKey(TemplateFamily, on_delete=models.PROTECT, related_name="templates")
    version = models.CharField(max_length=16, verbose_name="Версия (v1.0, v2.0…)")
    change_type = models.CharField(max_length=16, choices=ChangeType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    previous_template = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    superseded_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="supersedes"
    )

    approving_document = models.ForeignKey(
        NormativeDocument, on_delete=models.PROTECT, related_name="approved_templates",
        verbose_name="Утверждающий приказ",
    )
    revoking_document = models.ForeignKey(
        NormativeDocument, null=True, blank=True, on_delete=models.PROTECT,
        related_name="revoked_templates", verbose_name="Отменяющий приказ",
    )

    # working_storage — не WORM: минорная корректировка (ТЗ 4.3.1) заменяет
    # именно этот файл без прерывания жизненного цикла записи Template.
    file_editable = models.FileField(
        upload_to="templates/editable/%Y/", storage=working_storage,
        verbose_name="Защищённый рабочий бланк (.docx/.xlsx)",
    )
    file_sample = models.FileField(
        upload_to="templates/samples/%Y/", storage=working_storage,
        verbose_name="Эталонный образец заполнения (.pdf)",
    )

    download_count = models.PositiveIntegerField(default=0)
    last_reviewed_at = models.DateField(
        null=True, blank=True, verbose_name="Дата последнего пересмотра"
    )

    class Meta:
        verbose_name = "Бланк / шаблон"
        verbose_name_plural = "Бланки и шаблоны"
        ordering = ["family", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["family", "version"], name="unique_template_version"),
        ]

    def __str__(self):
        return f"{self.family.name} {self.version}"
