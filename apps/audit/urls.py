from django.urls import path

from . import export_views, views

app_name = "audit"

urlpatterns = [
    path("", views.AuditLogListView.as_view(), name="list"),
    path("export/", export_views.AuditLogExportView.as_view(), name="export"),
]
