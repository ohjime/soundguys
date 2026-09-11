from django.urls import path

from vote.views import (
    create_local_comment,
    local_discussion,
    submit_vote,
    vote_about,
    vote_initial,
    vote_tab,
    voter_index,
)

app_name = "vote"

urlpatterns = [
    path("", voter_index, name="vote"),
    path("initial/", vote_initial, name="vote_initial"),
    path("tab/", vote_tab, name="vote_tab"),
    path("about/", vote_about, name="about"),
    path("posts/<uuid:slug>/comments/", local_discussion, name="discussion"),
    path("posts/<uuid:slug>/comments/new/", create_local_comment, name="create_comment"),
    path("submit_vote/", submit_vote, name="submit_vote"),
]
