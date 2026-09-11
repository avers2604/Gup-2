"""Бизнес-логика банка бланков (ТЗ 4.3.1).

DDD-граница, объявленная в STACK.md: единственное место, где создаётся
версия бланка и учитывается скачивание. `views.py` и админка вызывают
эти функции, а не пишут в модель напрямую.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F

from apps.core.write_retry import retry_on_read_only_primary

from . import permissions
from .models import Template, TemplateFamily


class FilePromotionPending(ValidationError):
    """Файл опубликован в БД, но ещё не закреплён в WORM-хранилище."""


def publish_version(*, actor, family, form=None, **attrs):
    """Опубликовать новую версию бланка внутри семейства форм.

    Модель `Template` не черновик, а уже опубликованный артефакт (см. её
    docstring): ТЗ 4.3.1 требует инкремента версии даже для минорной
    корректировки. Поэтому «правки» тут нет — есть только выпуск новой
    строки, которая переводит предыдущую активную версию в
    `superseded` и связывается с ней в обе стороны.

    Файлы опубликованной версии сначала записываются в mutable staging
    (`working/staging/worm`) и попадают в `originals` с Governance/Legal
    Hold только после успешного commit. Поэтому rollback никогда не делает
    DELETE в WORM — он удаляет только staging-объекты.
    """
    if not permissions.can_manage_templates(actor):
        raise PermissionDenied(
            "Публикация версий бланков доступна Контролёру/Юристу и Администратору."
        )

    with transaction.atomic():
        TemplateFamily.objects.select_for_update().get(pk=family.pk)
        previous = (
            Template.objects.select_for_update()
            .filter(family=family, status=Template.Status.ACTIVE)
            .order_by("-created_at")
            .first()
        )

        template = form.save(commit=False) if form is not None else Template(**attrs)
        template.family = family
        template.status = Template.Status.ACTIVE
        template.previous_template = previous
        template._audit_actor = actor
        template.full_clean(exclude=["previous_template", "superseded_by", "family"])
        template.save()

        if previous is not None:
            previous.status = Template.Status.SUPERSEDED
            previous.superseded_by = template
            previous._audit_actor = actor
            try:
                previous.save(update_fields=["status", "superseded_by", "updated_at"])
            except Exception:
                from apps.core.staged_files import discard_staged_uploads

                discard_staged_uploads(template)
                raise

    return template


def prepare_download(*, actor, template, field_name):
    """Проверить право/готовность файла к выдаче, ничего не учитывая в БД.

    Этот шаг намеренно отделён от `register_download()`: HTTP-контур сначала
    должен убедиться, что объект storage действительно открывается, и только
    после этого увеличивать счётчик и писать выдачу архивной формы в WORM.
    Иначе немедленный отказ MinIO/S3 превращается в ложное «скачивание».
    """
    if not permissions.can_view_templates(actor):
        raise PermissionDenied("Требуется вход в систему.")

    field_file = getattr(template, field_name, None)
    if field_name not in {"file_editable", "file_sample"} or not field_file:
        raise ValidationError("У бланка нет такого файла.")

    from apps.core.staged_files import promotion_pending_for

    if promotion_pending_for(
        "templates_bank.template",
        str(template.pk),
        field_name,
        field_file.name,
    ):
        raise FilePromotionPending(
            "Файл ещё закрепляется в защищённом хранилище. Повторите скачивание позже."
        )

    return field_file


@retry_on_read_only_primary
def register_download(*, actor, template, field_name):
    """Учесть скачивание после подтверждения доступности файла.

    HTTP-view сначала открывает object storage через `prepare_download()`, а
    затем приходит сюда. Между этими двумя шагами версия может стать
    `SUPERSEDED`, поэтому решение о WORM-аудите нельзя принимать по переданному
    (возможно stale) экземпляру. Текущая строка перечитывается под row lock и
    именно её статус определяет, является ли выдача архивной.

    Повторный `prepare_download()` на заблокированной строке сохраняет
    сервисную границу для прямых вызовов и проверяет актуальное имя файла/
    состояние promotion перед учётом.
    """
    with transaction.atomic():
        current = (
            Template.objects.select_for_update()
            .select_related("family")
            .get(pk=template.pk)
        )
        field_file = prepare_download(
            actor=actor,
            template=current,
            field_name=field_name,
        )

        Template.objects.filter(pk=current.pk).update(
            download_count=F("download_count") + 1
        )

        if current.status == Template.Status.SUPERSEDED:
            from apps.audit.models import AuditLog

            AuditLog.objects.create(
                event_type=AuditLog.EventType.ARCHIVE_DOWNLOAD,
                actor=actor,
                actor_personnel_number=getattr(actor, "personnel_number", ""),
                object_type="Template",
                object_id=str(current.pk),
                details={
                    "family": current.family.name,
                    "version": current.version,
                    "field": field_name,
                },
            )

    return field_file


def family_lineage(family):
    """Все версии семейства от новой к старой (ТЗ 4.3.1, template_lineage)."""
    return (
        Template.objects.filter(family=family)
        .select_related("approving_document", "revoking_document", "previous_template")
        .order_by("-created_at")
    )
