from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.audit.models import AuditLog
from apps.core.antivirus import MalwareDetected
from apps.core.storage import originals_storage
from apps.core.tests.clamd_fixture import EICAR_BYTES, ClamdTestCase
from apps.documents.retention import RetentionMode
from apps.documents.tests.factories import make_document

from .models import Template, TemplateFamily


def _make_template(status=Template.Status.ACTIVE, version="v1.0", **kwargs):
    family = kwargs.pop("family", None) or TemplateFamily.objects.create(name="Акт схода подвижного состава")
    approving_document = kwargs.pop("approving_document", None) or make_document(reg_number=f"doc-{version}")
    defaults = dict(
        family=family, version=version, change_type=Template.ChangeType.MAJOR, status=status,
        approving_document=approving_document,
    )
    defaults.update(kwargs)
    return Template.objects.create(**defaults)


class TemplateStatusChangeAuditTests(TestCase):
    """Усиление аудита (решение Заказчика): Template.save() (раньше без
    переопределения save() вообще) пишет WORM-запись при реальном
    изменении status — тот же паттерн, что и NormativeDocument.status."""

    def test_creating_template_writes_no_audit_entry(self):
        _make_template()
        self.assertFalse(
            AuditLog.objects.filter(
                event_type__in=[AuditLog.EventType.TEMPLATE_SUPERSEDED, AuditLog.EventType.TEMPLATE_UPDATED]
            ).exists()
        )

    def test_active_to_superseded_writes_template_superseded(self):
        template = _make_template()
        template.status = Template.Status.SUPERSEDED
        template.save()

        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.TEMPLATE_SUPERSEDED)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().details, {"old_status": "active", "new_status": "superseded"})

    def test_resaving_same_status_writes_no_audit_entry(self):
        template = _make_template()
        template.download_count += 1
        template.save()
        self.assertFalse(AuditLog.objects.filter(event_type=AuditLog.EventType.TEMPLATE_SUPERSEDED).exists())

    def test_actor_is_captured_from_transient_attribute(self):
        from apps.iam.models import Department, User

        dept, _ = Department.objects.get_or_create(
            name="Служба движения", defaults={"level": Department.Level.SERVICE}
        )
        operator = User.objects.create(
            personnel_number="0099", last_name="Петров", first_name="Иван",
            position="Контролёр", department=dept, role=User.Role.CONTROLLER_LAWYER,
        )
        template = _make_template()
        template.status = Template.Status.SUPERSEDED
        template._audit_actor = operator
        template.save()

        entry = AuditLog.objects.get(event_type=AuditLog.EventType.TEMPLATE_SUPERSEDED)
        self.assertEqual(entry.actor_personnel_number, "0099")


class TemplateStorageTests(TestCase):
    """Двухступенчатая модель (STACK.md, ревью retention): каждая строка
    Template — уже опубликованная версия (ТЗ 4.3.1 требует инкремента
    версии даже для минорной корректировки), поэтому её файлы должны
    лежать в заблокированном originals, а не в working."""

    def test_file_editable_uses_originals_storage(self):
        self.assertIs(Template._meta.get_field("file_editable").storage, originals_storage())

    def test_file_sample_uses_originals_storage(self):
        self.assertIs(Template._meta.get_field("file_sample").storage, originals_storage())

    def test_retention_mode_is_governance(self):
        # Governance, а не Compliance — по замыслу матрицы должен оставаться
        # снимаемым привилегированной ролью (замена файла — это НОВАЯ строка,
        # но исторические версии всё равно должны быть снимаемы при
        # обоснованной необходимости, в отличие от, например, приказов).
        self.assertEqual(Template.RETENTION_MODE, RetentionMode.GOVERNANCE)


class TemplateAntivirusTests(ClamdTestCase):
    """Антивирусная проверка (ТЗ 4.7, apps/core/antivirus.py) на загрузке
    бланка — тот же принцип, что и NormativeDocument.save() (см.
    apps/documents/tests/test_antivirus_integration.py): проверка ДО
    super().save(), заражённый файл не попадает в WORM-бакет originals."""

    def test_clean_files_upload_succeeds(self):
        editable = SimpleUploadedFile("form.docx", b"clean editable content")
        sample = SimpleUploadedFile("form.pdf", b"%PDF-1.4 clean sample")
        template = _make_template(file_editable=editable, file_sample=sample)
        template.refresh_from_db()
        self.assertTrue(template.file_editable.name)

    def test_eicar_in_file_editable_blocks_save(self):
        infected = SimpleUploadedFile("form.docx", EICAR_BYTES)
        with self.assertRaises(MalwareDetected):
            _make_template(version="v-av-1", file_editable=infected)
        self.assertFalse(Template.objects.filter(version="v-av-1").exists())

    def test_eicar_in_file_sample_writes_audit_entry(self):
        infected = SimpleUploadedFile("form.pdf", EICAR_BYTES)
        with self.assertRaises(MalwareDetected):
            _make_template(version="v-av-2", file_sample=infected)
        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.UPLOAD_MALWARE_DETECTED)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().details["field"], "file_sample")
