from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("health/", views.health, name="health"),
    path("metrics/business/", views.business_metrics, name="business-metrics"),
    path("styleguide/", views.StyleguideView.as_view(), name="styleguide"),
]
