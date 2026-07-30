from django.urls import path

from . import views

urlpatterns = [
    path("", views.wizard, name="gcpconfig_wizard"),
]
