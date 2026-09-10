import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.audit.models import AuditLog
from apps.core.antivirus import MalwareDetected
from apps.core.macro_check import MacrosDetected
from apps.core.storage import originals_storage
from apps.core.tests.clamd_fixture import EICAR_BYTES, ClamdTestCase
from apps.documents.retention import RetentionMode
from apps.documents.tests.factories import make_document

from .models import Template, TemplateFamily


def _docx_bytes(with_macro: bool) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<document/>")
        if with_macro:
            zf.writestr("word/vbaProject.bin", b"fake vba bytecode")
    return buf.getvalue()


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


class TemplateMacroCheckTests(ClamdTestCase):
    """Структурная проверка на макросы (ТЗ 4.7, apps/core/macro_check.py)
    — тот же принцип, что и у NormativeDocument (см.
    apps/documents/tests/test_macro_check_integration.py)."""

    def test_docx_with_macro_blocks_save(self):
        infected = SimpleUploadedFile("form.docx", _docx_bytes(with_macro=True))
        with self.assertRaises(MacrosDetected):
            _make_template(version="v-mc-1", file_editable=infected)
        self.assertFalse(Template.objects.filter(version="v-mc-1").exists())

    def test_macro_rejection_writes_audit_entry(self):
        infected = SimpleUploadedFile("form.docx", _docx_bytes(with_macro=True))
        with self.assertRaises(MacrosDetected):
            _make_template(version="v-mc-2", file_editable=infected)
        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.UPLOAD_MACRO_REJECTED)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().details["field"], "file_editable")


# ---------------------------------------------------------------------------
# Рабочее место банка бланков (ТЗ 4.3) — партия 4 Этапа 2.
#
# До неё единственным входом в банк форм была Django admin, из которой
# линейный сотрудник не мог даже скачать действующий бланк — то есть ровно
# та операция, ради которой банк форм и заводится, интерфейса не имела.
# ---------------------------------------------------------------------------
from django.core.exceptions import PermissionDenied, ValidationError  # noqa: E402
from django.test import Client  # noqa: E402
from django.urls import reverse  # noqa: E402

from apps.documents.models import NormativeDocument  # noqa: E402
from apps.documents.tests.test_permissions import make_user  # noqa: E402
from apps.iam.models import User  # noqa: E402

from . import permissions, services  # noqa: E402

PASSWORD = "Sup3r$ecret!Pass"


class TemplatePermissionTests(TestCase):
    def test_any_employee_may_view(self):
        # Бланк — рабочий инструмент линейного персонала; ограничивать
        # просмотр было бы обратно смыслу банка форм.
        self.assertTrue(permissions.can_view_templates(make_user()))

    def test_reader_cannot_manage(self):
        self.assertFalse(permissions.can_manage_templates(make_user()))

    def test_methodist_cannot_manage(self):
        # Выпуск версии бланка — юридически значимое действие, тот же
        # круг ролей, что и у публикации НРД.
        user = make_user(personnel_number="0301", role=User.Role.METHODIST)
        self.assertFalse(permissions.can_manage_templates(user))

    def test_controller_can_manage(self):
        user = make_user(personnel_number="0302", role=User.Role.CONTROLLER_LAWYER)
        self.assertTrue(permissions.can_manage_templates(user))


class PublishVersionServiceTests(TestCase):
    def setUp(self):
        self.controller = make_user(personnel_number="0310", role=User.Role.CONTROLLER_LAWYER)
        self.family = TemplateFamily.objects.create(name="Акт осмотра контактной сети")
        self.approving = make_document(
            reg_number="T1-п", status=NormativeDocument.Status.ACTIVE,
        )

    def _attrs(self, version="v1.0"):
        return dict(
            version=version, change_type=Template.ChangeType.MAJOR,
            approving_document=self.approving,
            file_editable="templates/editable/2026/form.docx",
            file_sample="templates/samples/2026/sample.pdf",
        )

    def test_first_version_has_no_predecessor(self):
        template = services.publish_version(
            actor=self.controller, family=self.family, **self._attrs()
        )
        self.assertEqual(template.status, Template.Status.ACTIVE)
        self.assertIsNone(template.previous_template)

    def test_second_version_supersedes_the_first(self):
        first = services.publish_version(
            actor=self.controller, family=self.family, **self._attrs("v1.0")
        )
        second = services.publish_version(
            actor=self.controller, family=self.family, **self._attrs("v2.0")
        )
        first.refresh_from_db()
        self.assertEqual(first.status, Template.Status.SUPERSEDED)
        self.assertEqual(first.superseded_by, second)
        self.assertEqual(second.previous_template, first)

    def test_supersede_is_audited(self):
        services.publish_version(
            actor=self.controller, family=self.family, **self._attrs("v1.0")
        )
        services.publish_version(
            actor=self.controller, family=self.family, **self._attrs("v2.0")
        )
        entry = AuditLog.objects.get(event_type=AuditLog.EventType.TEMPLATE_SUPERSEDED)
        self.assertEqual(entry.actor_personnel_number, "0310")

    def test_reader_cannot_publish(self):
        with self.assertRaises(PermissionDenied):
            services.publish_version(
                actor=make_user(personnel_number="0311"),
                family=self.family, **self._attrs()
            )


class DownloadAccountingTests(TestCase):
    """ТЗ 4.3.1: учёт скачиваний и фиксация выдачи архивной формы."""

    def setUp(self):
        self.user = make_user(personnel_number="0320")
        self.template = _make_template(version="v9.0")
        self.template.file_editable = "templates/editable/2026/form.docx"
        self.template.save(update_fields=["file_editable"])

    def test_download_increments_counter(self):
        services.register_download(
            actor=self.user, template=self.template, field_name="file_editable"
        )
        self.template.refresh_from_db()
        self.assertEqual(self.template.download_count, 1)

    def test_active_version_download_is_not_audited(self):
        services.register_download(
            actor=self.user, template=self.template, field_name="file_editable"
        )
        self.assertFalse(
            AuditLog.objects.filter(event_type=AuditLog.EventType.ARCHIVE_DOWNLOAD).exists()
        )

    def test_superseded_version_download_is_audited(self):
        # Событие ARCHIVE_DOWNLOAD было заведено в модели журнала с
        # Этапа 1, но до этой партии его не писал никакой код.
        self.template.status = Template.Status.SUPERSEDED
        self.template.save(update_fields=["status"])
        services.register_download(
            actor=self.user, template=self.template, field_name="file_editable"
        )
        entry = AuditLog.objects.get(event_type=AuditLog.EventType.ARCHIVE_DOWNLOAD)
        self.assertEqual(entry.details["version"], "v9.0")
        self.assertEqual(entry.actor_personnel_number, "0320")

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ValidationError):
            services.register_download(
                actor=self.user, template=self.template, field_name="totp_secret"
            )


class TemplateBankViewTests(TestCase):
    def setUp(self):
        self.controller = make_user(personnel_number="0330", role=User.Role.CONTROLLER_LAWYER)
        self.reader = make_user(personnel_number="0331")
        self.family = TemplateFamily.objects.create(name="Путевой лист")
        self.template = _make_template(family=self.family, version="v1.0")
        self.template.file_editable = "templates/editable/2026/form.docx"
        self.template.save(update_fields=["file_editable"])

    def _client(self, user):
        client = Client()
        client.login(personnel_number=user.personnel_number, password=PASSWORD)
        return client

    def test_requires_login(self):
        response = Client().get(reverse("templates_bank:family_list"))
        self.assertEqual(response.status_code, 302)

    def test_reader_sees_list_without_management_buttons(self):
        response = self._client(self.reader).get(reverse("templates_bank:family_list"))
        self.assertContains(response, "Путевой лист")
        self.assertNotContains(response, "Новое семейство форм")

    def test_controller_sees_management_buttons(self):
        response = self._client(self.controller).get(reverse("templates_bank:family_list"))
        self.assertContains(response, "Новое семейство форм")

    def test_reader_cannot_open_version_form(self):
        response = self._client(self.reader).get(
            reverse("templates_bank:version_create", args=[self.family.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_family_detail_shows_lineage(self):
        response = self._client(self.reader).get(
            reverse("templates_bank:family_detail", args=[self.family.pk])
        )
        self.assertContains(response, "v1.0")
        self.assertContains(response, "Линия версий")

    def test_download_url_rejects_arbitrary_field_name(self):
        # Конвертер маршрута не пропускает ничего, кроме двух полей файла.
        from django.urls import NoReverseMatch

        with self.assertRaises(NoReverseMatch):
            reverse("templates_bank:download", args=[self.template.pk, "totp_secret"])

    def test_draft_approving_document_is_rejected(self):
        draft = make_document(reg_number="T2-п")
        response = self._client(self.controller).post(
            reverse("templates_bank:version_create", args=[self.family.pk]),
            {
                "version": "v2.0", "change_type": Template.ChangeType.MAJOR,
                "approving_document": str(draft.pk), "last_reviewed_at": "",
            },
        )
        self.assertContains(response, "не может быть черновиком")

    def test_duplicate_version_is_rejected_before_upload(self):
        response = self._client(self.controller).post(
            reverse("templates_bank:version_create", args=[self.family.pk]),
            {
                "version": "v1.0", "change_type": Template.ChangeType.MAJOR,
                "approving_document": str(self.template.approving_document.pk),
                "last_reviewed_at": "",
            },
        )
        self.assertContains(response, "уже выпущена")
