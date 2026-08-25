from django.http import HttpResponse
from django.shortcuts import render

from core.models import Listener, Sound
from core.utils import show_modal
from explore.renderer import get_explore_context
from library.models import SoundMix
from library.utils import get_empty_layer
from app.utils import build_artist_context


def example_card_page(request):
    return render(request, "example/example_card.html")


def example_card_initial(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")
    return render(
        request,
        "example/example_card.html#initial",
    )


def example_card_swap_figure(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")
    return render(
        request,
        "example/example_card.html#new-figure",
    )


def example_card_swap_body(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")
    return render(
        request,
        "example/example_card.html#new-body",
    )


def example_card_swap_header(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")
    return render(
        request,
        "example/example_card.html#new-header",
    )


def example_card_swap_multiple(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")
    return render(
        request,
        "example/example_card.html#new-multiple",
    )


def artist_details(request):
    """Open the artist_details modal for the artist behind a given sound."""
    if not request.htmx:
        return HttpResponse("Request Denied.")
    sound_id = request.GET.get("sound_id")
    sound = (
        Sound.objects.select_related("artist")
        .filter(pk=sound_id)
        .first()
        if sound_id
        else None
    )
    artist = sound.artist if sound else None
    fallback_name = sound.artist_name if sound else ""
    context = build_artist_context(request.user, artist, fallback_name)
    return show_modal(request, "app/artist_details.html", context)


def home_page(request):
    return render(request, "app/home.html")


def home_initial(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")
    return render(
        request,
        "app/home.html#initial",
        get_explore_context(request.user),
    )


def home_tab_library(request):
    """Render the LIBRARY tab over a fresh empty layer."""
    if not request.htmx:
        return HttpResponse("Request Denied.")

    liked_sound_count = 0
    saved_mix_count = 0
    if request.user.is_authenticated:
        listener = Listener.objects.filter(user=request.user).first()
        if listener is not None:
            liked_sound_count = listener.collection.count()
        saved_mix_count = SoundMix.objects.filter(creator=request.user).count()

    return render(
        request,
        "app/home.html#tab_library",
        {
            "sounds": [get_empty_layer()],
            "liked_sound_count": liked_sound_count,
            "saved_mix_count": saved_mix_count,
        },
    )


def home_tab_about(request):
    """The ABOUT tab's body: the static cosound write-up (no context needed)."""
    if not request.htmx:
        return HttpResponse("Request Denied.")
    return render(request, "app/home.html#tab_about")
