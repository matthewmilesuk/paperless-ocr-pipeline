from django.urls import path

from . import views

urlpatterns = [
    path("", views.upload, name="upload"),
    path("jobs/<int:job_id>/", views.job_status, name="job_status"),
]
