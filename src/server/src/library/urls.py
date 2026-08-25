from django.urls import path

from library.views import (
    library_save,
    library_save_confirm,
    library_keep_sound,
    library_liked_list,
    library_saved_list,
    library_delete_mix,
    library_swap,
    library_search,
    library_carousel,
)

app_name = "library"

urlpatterns = [
    path("save/", library_save, name="save"),
    path("save/confirm/", library_save_confirm, name="save_confirm"),
    path("keep-sound/", library_keep_sound, name="keep_sound"),
    path("liked-list/", library_liked_list, name="liked_list"),
    path("saved-list/", library_saved_list, name="saved_list"),
    path("saved-mixes/<int:mix_id>/delete/", library_delete_mix, name="delete_mix"),
    path("swap/", library_swap, name="swap"),
    path("search/", library_search, name="search"),
    path("carousel/", library_carousel, name="carousel"),
]
