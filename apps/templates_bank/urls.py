from django.urls import path, register_converter

from . import views


class FileFieldConverter:
    """Только два поля файла и допустимы в адресе скачивания.

    Иначе имя поля пришло бы из URL произвольной строкой, и запрос вида
    `/templates/<id>/download/totp_secret/` попытался бы отдать всё, что
    у модели окажется под этим именем. Валидация в сервисе есть и без
    этого, но лишний слой здесь стоит одной строки.
    """

    regex = "file_editable|file_sample"

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


register_converter(FileFieldConverter, "templatefile")

app_name = "templates_bank"

urlpatterns = [
    path("", views.TemplateFamilyListView.as_view(), name="family_list"),
    path("new/", views.TemplateFamilyCreateView.as_view(), name="family_create"),
    path("<uuid:pk>/", views.TemplateFamilyDetailView.as_view(), name="family_detail"),
    path(
        "<uuid:pk>/versions/new/",
        views.TemplateVersionCreateView.as_view(), name="version_create",
    ),
    path(
        "versions/<uuid:pk>/download/<templatefile:field_name>/",
        views.TemplateDownloadView.as_view(), name="download",
    ),
]
