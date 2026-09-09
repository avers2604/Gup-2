import datetime

from apps.iam.models import Department

from ..models import NormativeDocument
from ..retention import RetentionCategory


def make_document(reg_number="142-п", **kwargs):
    dept, _ = Department.objects.get_or_create(
        name="Служба движения", defaults={"level": Department.Level.SERVICE}
    )
    defaults = dict(
        reg_number=reg_number,
        reg_date=datetime.date(2026, 1, 1),
        effective_date=datetime.date(2026, 1, 2),
        doc_type=NormativeDocument.DocType.ORDER,
        title=f"Тестовый документ {reg_number}",
        issuer_dept=dept,
        retention_category=RetentionCategory.ORDERS_CORE,
    )
    defaults.update(kwargs)
    return NormativeDocument.objects.create(**defaults)
