"""
Сидинг верхних двух уровней оргструктуры (ТЗ 4.6: Аппарат управления →
Службы → Парки/Районы → Линейные участки/подстанции).

Названия — не придуманы, а взяты дословно из текста ТЗ: п.1.3
(«Эксплуатирующие службы») и таблица в разделе 3. Уровни 3–4 (конкретные
парки/районы и участки/подстанции) в ТЗ не перечислены поимённо — их
даёт обследование служб на Этапе 1, поэтому здесь не сидируются.
"""
from django.db import migrations

SERVICES = [
    "Служба движения",
    "Служба подвижного состава",
    "Энергохозяйство",
    "Служба пути",
    "Служба АСиИТ",
    "Служба ОТ, ПБ и БДД",
]


def seed_departments(apps, schema_editor):
    Department = apps.get_model("iam", "Department")
    head_office, _ = Department.objects.get_or_create(
        name="Аппарат управления", level=1, parent=None
    )
    for name in SERVICES:
        Department.objects.get_or_create(name=name, level=2, parent=head_office)


def unseed_departments(apps, schema_editor):
    Department = apps.get_model("iam", "Department")
    Department.objects.filter(name__in=SERVICES, level=2).delete()
    Department.objects.filter(name="Аппарат управления", level=1).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("iam", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_departments, unseed_departments),
    ]
