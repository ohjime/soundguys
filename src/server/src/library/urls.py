from django.urls import path

from library.views import library_index, library_initial

app_name = "library"

urlpatterns = [
    path("", library_index, name="index"),
    path("htmx/initial", library_initial, name="initial"),
]
