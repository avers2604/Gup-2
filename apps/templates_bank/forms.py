"""Формы рабочего места банка бланков (ТЗ 4.3). Валидация ввода, без
бизнес-логики — тот же принцип, что в apps/documents/forms.py."""
from django import forms

from apps.documents.models import NormativeDocument

from .models import Template, TemplateFamily

_FIELD_ATTRS = {"class": "field"}


class TemplateFamilyForm(forms.ModelForm):
    """Заведение семейства форм — единого GUID жизненного цикла бланка
    (lineage_root_id, ТЗ 4.3.1)."""

    class Meta:
        model = TemplateFamily
        fields = ["name"]
        widgets = {"name": forms.TextInput(attrs=_FIELD_ATTRS)}


class TemplateVersionForm(forms.ModelForm):
    """Новая версия бланка внутри семейства.

    `family`, `status`, `previous_template` и `superseded_by` в форме
    отсутствуют: семейство задаётся адресом страницы, а остальные три
    проставляет сервис при публикации — это связи жизненного цикла, а не
    ввод пользователя.
    """

    class Meta:
        model = Template
        fields = [
            "version", "change_type", "approving_document", "revoking_document",
            "file_editable", "file_sample", "last_reviewed_at",
        ]
        widgets = {
            "version": forms.TextInput(attrs={**_FIELD_ATTRS, "placeholder": "v1.0"}),
            "change_type": forms.Select(attrs=_FIELD_ATTRS),
            "approving_document": forms.Select(attrs=_FIELD_ATTRS),
            "revoking_document": forms.Select(attrs=_FIELD_ATTRS),
            "last_reviewed_at": forms.DateInput(attrs={**_FIELD_ATTRS, "type": "date"}),
        }

    def __init__(self, *args, user=None, family=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.family = family

        # Утверждающий и отменяющий приказы выбираются только из
        # документов, доступных пользователю: иначе через выпадающий
        # список банка бланков утекали бы номера карточек «ДСП».
        from apps.documents import permissions as document_permissions

        visible = document_permissions.visible_documents(user).order_by("-reg_date")
        self.fields["approving_document"].queryset = visible
        self.fields["revoking_document"].queryset = visible

        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.ClearableFileInput):
                widget.attrs.setdefault("class", "field")

    def clean_version(self):
        version = self.cleaned_data["version"].strip()
        if self.family is not None and Template.objects.filter(
            family=self.family, version=version
        ).exists():
            # UniqueConstraint(family, version) поймает это и в БД, но
            # его сообщение пришло бы после отправки файлов — а они
            # к тому моменту уже прошли антивирус и запись в хранилище.
            raise forms.ValidationError(
                "Такая версия в этом семействе форм уже выпущена."
            )
        return version

    def clean(self):
        cleaned = super().clean()
        approving = cleaned.get("approving_document")
        if approving is not None and approving.status == NormativeDocument.Status.DRAFT:
            # Форму утверждает документ, а не проект документа: черновик
            # юридической силы не имеет и утвердить бланк не может.
            self.add_error(
                "approving_document",
                "Утверждающий документ не может быть черновиком — сначала опубликуйте его.",
            )
        return cleaned
