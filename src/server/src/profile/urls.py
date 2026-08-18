from django.urls import path

from profile.views import profile_modal


app_name = "profile"

urlpatterns = [
    path("", profile_modal, name="profile_modal"),
]
