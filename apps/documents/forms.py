"""Формы Web GUI рабочих мест (ТЗ 4.1). Валидация ввода, без бизнес-логики
— тот же принцип, что и в apps/iam/forms.py."""
from django import forms

from apps.iam.models import Department

from .models import DocumentRelation, NormativeDocument

# .field — класс дизайн-системы (static/css/components.css), тот же
# паттерн ручного проставления, что в apps/iam/forms.py и
# apps/search_ocr/forms.py.
_FIELD_ATTRS = {"class": "field"}


class DocumentFilterForm(forms.Form):
    """Фильтры реестра НРД. Все поля необязательны: пустая форма — это
    валидная форма «показать всё, что доступно», а не ошибка ввода."""

    doc_type = forms.ChoiceField(
        label="Вид документа", required=False,
        choices=[("", "Любой")] + list(NormativeDocument.DocType.choices),
        widget=forms.Select(attrs=_FIELD_ATTRS),
    )
    status = forms.ChoiceField(
        label="Статус", required=False,
        choices=[("", "Любой")] + list(NormativeDocument.Status.choices),
        widget=forms.Select(attrs=_FIELD_ATTRS),
    )
    issuer_dept = forms.ModelChoiceField(
        label="Издавшее подразделение", required=False,
        queryset=Department.objects.all(), empty_label="Любое",
        widget=forms.Select(attrs=_FIELD_ATTRS),
    )
    effective_from = forms.DateField(
        label="Действует с", required=False,
        widget=forms.DateInput(attrs={**_FIELD_ATTRS, "type": "date"}),
    )
    effective_to = forms.DateField(
        label="Действует по", required=False,
        widget=forms.DateInput(attrs={**_FIELD_ATTRS, "type": "date"}),
    )

    def clean(self):
        cleaned = super().clean()
        date_from = cleaned.get("effective_from")
        date_to = cleaned.get("effective_to")
        if date_from and date_to and date_from > date_to:
            # Иначе фильтр молча вернул бы пустой список, и пользователь
            # решил бы, что документов нет, а не что интервал перевёрнут.
            raise forms.ValidationError("Начало периода позже его окончания.")
        return cleaned


class DocumentForm(forms.ModelForm):
    """Создание и правка карточки НРД (ТЗ 4.2.1).

    Статуса в форме нет намеренно: создание всегда даёт черновик, а смена
    статуса — отдельное действие с другим кругом полномочий
    (`permissions.can_change_status`, `StatusChangeForm` ниже). Нет и
    `retention_mode`/`retention_until` — они `editable=False` и считаются
    в `save()` из категории хранения; показать их редактируемыми значило
    бы предложить пользователю править юридически значимую дату руками.
    """

    revision = forms.IntegerField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = NormativeDocument
        fields = [
            "reg_number", "reg_date", "effective_date", "doc_type", "title", "summary",
            "issuer_dept", "applied_depts", "category_tags",
            "access_level", "declassification_date",
            "retention_category", "ocr_category",
            "files_original", "files_editable",
        ]
        widgets = {
            "reg_date": forms.DateInput(attrs={"type": "date"}),
            "effective_date": forms.DateInput(attrs={"type": "date"}),
            "declassification_date": forms.DateInput(attrs={"type": "date"}),
            "summary": forms.Textarea(attrs={"rows": 4}),
        }

    # Группировка полей для шаблона. Живёт рядом со списком полей, а не в
    # шаблоне: добавив поле в Meta.fields и забыв про шаблон, легко
    # получить поле, которое валидируется и сохраняется, но не
    # показывается — fieldsets() ниже такое поле обнаружит.
    FIELDSETS = (
        ("Реквизиты", "", [
            "reg_number", "reg_date", "effective_date", "doc_type",
            "title", "summary", "issuer_dept", "applied_depts", "category_tags",
        ]),
        ("Доступ и хранение", "", [
            "access_level", "declassification_date", "retention_category", "ocr_category",
        ]),
        ("Файлы", (
            "Скан-оригинал уходит в WORM-хранилище и запускает распознавание. "
            "Оба файла проверяются антивирусом и на встроенные макросы до записи "
            "в хранилище — заражённый файл не сохраняется вовсе."
        ), ["files_original", "files_editable"]),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["revision"].initial = self.instance.edit_version
        self.fields["revision"].required = not self.instance._state.adding
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} field".strip()

    def fieldsets(self):
        """Поля, разложенные по группам для шаблона.

        Поле, объявленное в Meta.fields, но не попавшее ни в одну группу,
        выводится последней группой «Прочее», а не теряется молча:
        невидимое поле формы — это поле, которое пользователь не может
        заполнить, но которое от него требуют.
        """
        grouped = {"revision"}
        for title, hint, names in self.FIELDSETS:
            fields = [self[name] for name in names if name in self.fields]
            grouped.update(field.name for field in fields)
            if fields:
                yield {"title": title, "hint": hint, "fields": fields}

        rest = [self[name] for name in self.fields if name not in grouped]
        if rest:
            yield {"title": "Прочее", "hint": "", "fields": rest}

    def clean(self):
        cleaned = super().clean()
        access_level = cleaned.get("access_level")
        declassification_date = cleaned.get("declassification_date")
        if (
            declassification_date
            and access_level != NormativeDocument.AccessLevel.RESTRICTED
        ):
            # Дата рассекречивания у документа без грифа не имеет смысла и
            # молча искажает расчёт срока хранения: retention.py
            # отсчитывает срок ДСП-документа именно от неё.
            self.add_error(
                "declassification_date",
                "Дата рассекречивания указывается только для документов с грифом «ДСП».",
            )

        reg_date = cleaned.get("reg_date")
        effective_date = cleaned.get("effective_date")
        if reg_date and effective_date and effective_date < reg_date:
            self.add_error(
                "effective_date",
                "Документ не может вступить в силу раньше даты своей регистрации.",
            )
        return cleaned


class StatusChangeForm(forms.Form):
    """Смена статуса карточки. Список вариантов строится из графа
    переходов (`transitions.py`) для ТЕКУЩЕГО статуса документа —
    недопустимый переход невозможно даже выбрать. Сервис всё равно
    проверяет переход заново: форму можно обойти, отправив запрос
    напрямую."""

    new_status = forms.ChoiceField(
        label="Новый статус", choices=(),
        widget=forms.Select(attrs=_FIELD_ATTRS),
    )
    comment = forms.CharField(
        label="Основание", required=False,
        widget=forms.Textarea(attrs={**_FIELD_ATTRS, "rows": 3}),
    )

    def __init__(self, *args, document=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from . import permissions, transitions

        self.document = document
        self.user = user
        choices = transitions.target_choices(document.status) if document is not None else []
        # Переходы, недоступные этой роли, из списка убираются: откат
        # публикации и аннулирование оставлены Администратору, и
        # предлагать их Контролёру/Юристу — предлагать заведомый отказ.
        if user is not None and document is not None:
            choices = [
                (value, label) for value, label in choices
                if permissions.can_change_status(user, document, value)
            ]
        self.fields["new_status"].choices = choices
        if not choices:
            self.fields["new_status"].widget.attrs["disabled"] = True

        self.fields["comment"].help_text = (
            "Обязательно для отката публикации и аннулирования — "
            "иначе необязательное пояснение для журнала аудита."
        )

    def clean(self):
        cleaned = super().clean()
        from .transitions import requires_reason

        new_status = cleaned.get("new_status")
        if new_status and requires_reason(new_status) and not (cleaned.get("comment") or "").strip():
            self.add_error(
                "comment",
                "Для отката публикации и аннулирования основание обязательно.",
            )
        return cleaned


class RelationForm(forms.ModelForm):
    """Новое ребро графа версионности (ТЗ 4.2.2).

    Список документов-целей строится из видимых пользователю и без самой
    карточки: самоссылку ловит ограничение БД, но предлагать её в
    выпадающем списке — значит предлагать заведомую ошибку.
    """

    class Meta:
        model = DocumentRelation
        fields = ["to_document", "relation_type", "note"]
        widgets = {
            "to_document": forms.Select(attrs=_FIELD_ATTRS),
            "relation_type": forms.Select(attrs=_FIELD_ATTRS),
            "note": forms.TextInput(attrs=_FIELD_ATTRS),
        }
        labels = {"to_document": "Документ", "note": "Затронутые пункты"}

    def __init__(self, *args, user=None, from_document=None, **kwargs):
        super().__init__(*args, **kwargs)
        from . import permissions

        self.from_document = from_document
        queryset = permissions.visible_documents(user).order_by("-reg_date", "reg_number")
        if from_document is not None:
            queryset = queryset.exclude(pk=from_document.pk)
        self.fields["to_document"].queryset = queryset

    def clean(self):
        cleaned = super().clean()
        if self.from_document is not None:
            # Проставляем сторону-источник до валидации модели: без неё
            # DocumentRelation.clean() не сможет проверить цикл, а
            # ModelForm вызывает её в _post_clean().
            self.instance.from_document = self.from_document
        return cleaned
