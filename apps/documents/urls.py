from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("", views.DocumentListView.as_view(), name="list"),
    path("new/", views.DocumentCreateView.as_view(), name="create"),
    # "new/" объявлен ДО "<uuid:pk>/": иначе конвертер uuid всё равно не
    # совпал бы со словом, но порядок делает намерение явным и защищает от
    # будущей замены конвертера на более широкий.
    path("<uuid:pk>/", views.DocumentDetailView.as_view(), name="detail"),
    path("<uuid:pk>/edit/", views.DocumentUpdateView.as_view(), name="edit"),
    path("<uuid:pk>/status/", views.DocumentStatusChangeView.as_view(), name="status"),
]
