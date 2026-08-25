from django.urls import path

from app.views import (
    example_card_page,
    example_card_initial,
    example_card_swap_figure,
    example_card_swap_body,
    example_card_swap_header,
    example_card_swap_multiple,
    home_page,
    home_initial,
    home_tab_library,
    home_tab_about,
    artist_details,
)

app_name = "app"

urlpatterns = [
    path(
        "",
        home_page,
        name="home_page",
    ),
]

example_urlpatterns = [
    path(
        "examples/card",
        example_card_page,
        name="example_card_page",
    ),
]

htmx_urlpatterns = [
    path(
        "htmx/examples/card/initial",
        example_card_initial,
        name="example_card_initial",
    ),
    path(
        "htmx/examples/card/swap-figure",
        example_card_swap_figure,
        name="example_card_swap_figure",
    ),
    path(
        "htmx/examples/card/swap-body",
        example_card_swap_body,
        name="example_card_swap_body",
    ),
    path(
        "htmx/examples/card/swap-header",
        example_card_swap_header,
        name="example_card_swap_header",
    ),
    path(
        "htmx/examples/card/swap-multiple",
        example_card_swap_multiple,
        name="example_card_swap_multiple",
    ),
    path(
        "htmx/home/initial",
        home_initial,
        name="home_initial",
    ),
    path(
        "htmx/home/tab/library",
        home_tab_library,
        name="home_tab_library",
    ),
    path(
        "htmx/home/tab/about",
        home_tab_about,
        name="home_tab_about",
    ),
    path(
        "htmx/artist/details",
        artist_details,
        name="artist_details",
    ),
]

urlpatterns += htmx_urlpatterns + example_urlpatterns
