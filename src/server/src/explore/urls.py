from django.urls import path

from explore.views import (
    create_comment,
    explore_detail,
    explore_discussion,
    explore_index,
)

app_name = "explore"

urlpatterns = [
    path("", explore_index, name="index"),
    path("<uuid:slug>/comments/", explore_discussion, name="discussion"),
    path("<uuid:slug>/comments/new/", create_comment, name="create_comment"),
    path("<uuid:slug>/", explore_detail, name="detail"),
]
