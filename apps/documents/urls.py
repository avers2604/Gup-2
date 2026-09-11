from django.urls import path

from . import file_views, views

app_name = "documents"

urlpatterns = [
    path("", views.DocumentListView.as_view(), name="list"),
    path("new/", views.DocumentCreateView.as_view(), name="create"),
    path("consolidated/", views.ConsolidatedListView.as_view(), name="consolidated_list"),
    path(
        "consolidated/<uuid:pk>/",
        views.ConsolidatedDetailView.as_view(),
        name="consolidated_detail",
    ),
    path("ocr-review/", views.OcrReviewQueueView.as_view(), name="ocr_review_queue"),
    path("ocr-review/<uuid:pk>/", views.OcrReviewView.as_view(), name="ocr_review"),
    # "new/" объявлен ДО "<uuid:pk>/": иначе конвертер uuid всё равно не
    # совпал бы со словом, но порядок делает намерение явным и защищает от
    # будущей замены конвертера на более широкий.
    path("<uuid:pk>/files/<str:kind>/", file_views.document_file_link, name="file-link"),
    path("<uuid:pk>/", views.DocumentDetailView.as_view(), name="detail"),
    path("<uuid:pk>/edit/", views.DocumentUpdateView.as_view(), name="edit"),
    path("<uuid:pk>/status/", views.DocumentStatusChangeView.as_view(), name="status"),
    path(
        "<uuid:pk>/relations/new/",
        views.DocumentRelationCreateView.as_view(), name="relation_create",
    ),
    path(
        "<uuid:pk>/relations/<int:relation_id>/delete/",
        views.DocumentRelationDeleteView.as_view(), name="relation_delete",
    ),
]
