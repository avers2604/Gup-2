from django import template

from apps.core.staged_files import promotion_pending_for

register = template.Library()


@register.filter
def worm_file_pending(instance, field_name: str) -> bool:
    """True while the current immutable field has not reached originals yet."""
    field_file = getattr(instance, field_name, None)
    if not field_file or not getattr(field_file, "name", ""):
        return False
    return promotion_pending_for(
        instance._meta.label_lower,
        str(instance.pk),
        field_name,
        field_file.name,
    )


@register.filter
def worm_file_ready(instance, field_name: str) -> bool:
    field_file = getattr(instance, field_name, None)
    return bool(field_file and getattr(field_file, "name", "")) and not worm_file_pending(
        instance, field_name
    )
