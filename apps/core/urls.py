from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("styleguide/", views.StyleguideView.as_view(), name="styleguide"),
]
