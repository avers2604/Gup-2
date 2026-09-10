from django.urls import path

from .api import DocumentSearchAPIView

app_name = "search_ocr_api"

urlpatterns = [
    path("documents/", DocumentSearchAPIView.as_view(), name="document-search"),
]
