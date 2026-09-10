from django.urls import path

from . import views

app_name = "search_ocr"

urlpatterns = [
    path("", views.SearchView.as_view(), name="search"),
]
