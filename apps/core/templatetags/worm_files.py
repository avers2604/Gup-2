from django import template

from apps.core.staged_files import promotion_pending_for

register = template.Library()

_CACHE_ATTR = "_worm_file_pending_cache"


def _promotion_pending(instance, field_name: str, field_file) -> bool:
    """Resolve promotion state once per object/field/file-name combination.

    Templates commonly ask both `worm_file_pending` and `worm_file_ready` for
    the same immutable file. Without a per-instance cache each filter performs
    the same StagedFilePromotion.exists() query, doubling database work across
    document/template lists. The file name is part of the key so replacing a
    file on the same in-memory model instance cannot reuse stale state.
    """
    key = (field_name, field_file.name)
    cache = getattr(instance, _CACHE_ATTR, None)
    if cache is None:
        cache = {}
        setattr(instance, _CACHE_ATTR, cache)
    if key not in cache:
        cache[key] = promotion_pending_for(
            instance._meta.label_lower,
            str(instance.pk),
            field_name,
            field_file.name,
        )
    return cache[key]


@register.filter
def worm_file_pending(instance, field_name: str) -> bool:
    """True while the current immutable field has not reached originals yet."""
    field_file = getattr(instance, field_name, None)
    if not field_file or not getattr(field_file, "name", ""):
        return False
    return _promotion_pending(instance, field_name, field_file)


@register.filter
def worm_file_ready(instance, field_name: str) -> bool:
    field_file = getattr(instance, field_name, None)
    return bool(field_file and getattr(field_file, "name", "")) and not _promotion_pending(
        instance, field_name, field_file
    )
