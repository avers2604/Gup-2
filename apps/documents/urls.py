from django.urls import path

from . import file_views, views

app_name = "documents"

urlpatterns = [
    path("", views.DocumentListView.as_view(), name="list"),
    path("<uuid:pk>/files/<str:kind>/", file_views.document_file_link, name="file-link"),
    path("<uuid:pk>/", views.DocumentDetailView.as_view(), name="detail"),
]
