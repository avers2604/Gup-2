"""Признак доступа к вычитке OCR для навигации.

Тот же приём, что у `apps/audit/context_processors.py`: показывать пункт меню
решает та же функция прав, что закрывает саму страницу. Иначе меню и вьюха
разъезжаются, и пользователь видит ссылку, ведущую в 404.
"""
from . import permissions


def ocr_review_access(request):
    user = getattr(request, "user", None)
    return {"can_review_ocr": permissions.can_review_ocr(user) if user else False}
