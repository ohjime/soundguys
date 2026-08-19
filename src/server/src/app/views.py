from django.http import HttpResponse
from django.shortcuts import render

from core.models import Sound
from core.utils import show_modal
from mixer.utils import get_random_sounds
from app.utils import serialize_user_mixes, build_artist_context


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
        {
            "sounds": get_random_sounds(user=request.user),
            "user_mixes": serialize_user_mixes(request.user),
        },
    )


def home_tab_mixer(request):
    """The LISTEN tab's two-stage body over a fresh random mix."""
    if not request.htmx:
        return HttpResponse("Request Denied.")
    return render(
        request,
        "app/home.html#tab_mixer",
        {
            "sounds": get_random_sounds(user=request.user),
            "user_mixes": serialize_user_mixes(request.user),
        },
    )


def home_tab_about(request):
    """The ABOUT tab's body: the static cosound write-up (no context needed)."""
    if not request.htmx:
        return HttpResponse("Request Denied.")
    return render(request, "app/home.html#tab_about")
