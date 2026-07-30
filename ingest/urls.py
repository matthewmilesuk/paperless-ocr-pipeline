from django.urls import path

from . import views

urlpatterns = [
    path("", views.upload, name="upload"),
    path("jobs/", views.job_list, name="job_list"),
    path("jobs/<int:job_id>/", views.job_status, name="job_status"),
]
