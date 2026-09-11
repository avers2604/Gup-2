"""Бизнес-логика банка бланков (ТЗ 4.3.1).

DDD-граница, объявленная в STACK.md: единственное место, где создаётся
версия бланка и учитывается скачивание. `views.py` и админка вызывают
эти функции, а не пишут в модель напрямую.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F

from . import permissions
from .models import Template, TemplateFamily


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
        # Блокировка семейства: без неё две одновременные публикации
        # обе увидели бы одну и ту же «текущую активную» версию и обе
        # объявили бы себя её преемником — предыдущая версия получила бы
        # два superseded_by, а один из выпусков потерял бы связь с
        # предшественником. UniqueConstraint(family, version) такую
        # гонку не ловит: версии-то разные.
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
                # The new version has not reached Object-Locked storage yet.
                # Its staging files are mutable and can be safely discarded;
                # the promotion/outbox rows roll back with this transaction.
                from apps.core.staged_files import discard_staged_uploads

                discard_staged_uploads(template)
                raise

    return template


def register_download(*, actor, template, field_name):
    """Учесть скачивание файла бланка (ТЗ 4.3.1, `download_count`).

    Счётчик увеличивается через `F()`, а не чтением-записью в Python:
    два одновременных скачивания иначе записали бы одно и то же новое
    значение, и одно из них потерялось бы.

    Скачивание архивной (заменённой) версии дополнительно попадает в
    WORM-журнал событием `ARCHIVE_DOWNLOAD`. Событие было заведено в
    модели журнала с Этапа 1, но до этой партии его не писал никакой код
    — учитывать обращения к устаревшим формам и было незачем, пока
    выдавать их было неоткуда.
    """
    if not permissions.can_view_templates(actor):
        raise PermissionDenied("Требуется вход в систему.")

    field_file = getattr(template, field_name, None)
    if field_name not in {"file_editable", "file_sample"} or not field_file:
        raise ValidationError("У бланка нет такого файла.")

    with transaction.atomic():
        Template.objects.filter(pk=template.pk).update(
            download_count=F("download_count") + 1
        )

        if template.status == Template.Status.SUPERSEDED:
            from apps.audit.models import AuditLog

            AuditLog.objects.create(
                event_type=AuditLog.EventType.ARCHIVE_DOWNLOAD,
                actor=actor,
                actor_personnel_number=getattr(actor, "personnel_number", ""),
                object_type="Template",
                object_id=str(template.pk),
                details={
                    "family": template.family.name,
                    "version": template.version,
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
