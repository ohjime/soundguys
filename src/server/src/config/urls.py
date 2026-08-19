from django.contrib import admin
from django.urls import path, include

from app.api import api_urls


urlpatterns = [
    path("", include("app.urls")),
    path("api/", api_urls),
    path("login/", include("login.urls")),
    path("mixer/", include("mixer.urls")),
    path("vote/", include("vote.urls")),
    path("studio/", include("studio.urls")),
    path("profile/", include("profile.urls")),
    path("library/", include("library.urls")),
    path("admin/", admin.site.urls, name="admin"),
    path("upload/", include("django_file_form.urls")),
]
