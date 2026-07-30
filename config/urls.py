from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import include, path
from two_factor.urls import urlpatterns as tf_urls

urlpatterns = [
    path("admin/", admin.site.urls),
    path("django-rq/", include("django_rq.urls")),
    path("accounts/logout/", LogoutView.as_view(next_page="two_factor:login"), name="logout"),
    path("", include(tf_urls)),
    path("", include("ingest.urls")),
]
