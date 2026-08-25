import json
import uuid

from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from core.models import Cosound, Listener, Sound
from core.utils import add_card, close_modal, show_modal
from app.utils import serialize_mix
from login.views import login_modal
from library.models import SoundMix
from library.utils import parse_layers, serialize_sounds


def library_save(request):

    if not request.htmx:
        return HttpResponse("Request Denied.")

    if not request.user.is_authenticated:
        response = add_card(
            target_deck="deck",
            template="login/index.html#card",
            request=request,
        )
        response["HX-Trigger"] = "auth-required"
        return response

    layer_data, layers = parse_layers(request.POST.get("layers"))
    if layer_data is None:
        return HttpResponse("Invalid data.", status=400)
    if not layers:
        return HttpResponse("No layers provided.", status=400)

    hashid = Cosound.compute_hashid(layers)
    existing = SoundMix.objects.filter(
        creator=request.user, cosound__hashid=hashid
    ).first()

    # What the title box opens on. A mix this listener has already named keeps
    # that name; otherwise the card may suggest one — an Explore post sends its
    # own title, so keeping a copy of the explore mix does not ask a listener
    # to name something they did not build. The library's own card sends nothing
    # and the box opens empty.
    suggested_title = (request.POST.get("title") or "").strip()
    existing_title = (existing.title if existing else "") or suggested_title

    return show_modal(
        request,
        "app/home.html#mix_title_modal",
        {
            "layers_json": json.dumps(layer_data),
            "existing_title": existing_title[:255],
        },
    )


def library_save_confirm(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")

    if not request.user.is_authenticated:
        return HttpResponse("Request Denied.", status=401)

    _, layers = parse_layers(request.POST.get("layers"))
    if not layers:
        return HttpResponse("No layers provided.", status=400)

    title = (request.POST.get("title") or "").strip()

    cosound = Cosound.get_or_create_from_layers(layers)
    sound_mix, created = SoundMix.objects.get_or_create(
        creator=request.user, cosound=cosound
    )
    sound_mix.title = title
    sound_mix.save(update_fields=["title", "updated_at"])

    if not created:
        return close_modal(request)

    response = close_modal(request)
    response["HX-Trigger"] = json.dumps(
        {
            "close-modal": True,
            "mix-saved": {"mix": serialize_mix(sound_mix)},
        }
    )
    return response


def library_keep_sound(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")

    if not request.user.is_authenticated:
        response = login_modal(request)
        response["HX-Trigger"] = "auth-required"
        return response

    sound_id = request.POST.get("sound_id")
    try:
        sound = Sound.objects.get(pk=sound_id)
    except Sound.DoesNotExist:
        return HttpResponse("Sound not found.", status=404)

    listener, _ = Listener.objects.get_or_create(user=request.user)
    if listener.collection.filter(pk=sound.pk).exists():
        listener.collection.remove(sound)
        saved = False
    else:
        listener.collection.add(sound)
        saved = True

    response = HttpResponse("")
    response["HX-Reswap"] = "none"
    response["HX-Trigger"] = json.dumps(
        {"layer-saved": {"saved": saved, "soundId": sound_id}}
    )
    return response


def library_liked_list(request):
    """Push a listener's liked sounds onto the library deck."""
    if not request.htmx:
        return HttpResponse("Request Denied.")

    liked_sounds = []
    if request.user.is_authenticated:
        listener = Listener.objects.filter(user=request.user).first()
        if listener is not None:
            liked_sounds = listener.collection.select_related("artist").order_by(
                "title"
            )

    return add_card(
        target_deck="deck",
        template="library/library_liked_list.html",
        request=request,
        context={"liked_sounds": liked_sounds},
    )


def library_saved_list(request):
    """Push a listener's saved mixes onto the library deck."""
    if not request.htmx:
        return HttpResponse("Request Denied.")

    saved_mixes = []
    if request.user.is_authenticated:
        mixes = (
            SoundMix.objects.filter(creator=request.user)
            .select_related("cosound")
            .prefetch_related("cosound__soundlayer_set__sound__tags")
            .order_by("-created_at")
        )
        saved_mixes = [serialize_mix(mix) for mix in mixes]

    return add_card(
        target_deck="deck",
        template="library/library_saved_list.html",
        request=request,
        context={
            "saved_mixes": saved_mixes,
            "saved_mix_data_id": f"savedMixCardData-{uuid.uuid4().hex}",
        },
    )


def library_delete_mix(request, mix_id):
    """Delete one of the signed-in listener's saved mixes."""
    if not request.htmx:
        return HttpResponse("Request Denied.")
    if request.method != "POST":
        return HttpResponse(status=405)
    if not request.user.is_authenticated:
        return HttpResponse("Request Denied.", status=401)

    sound_mix = get_object_or_404(SoundMix, pk=mix_id, creator=request.user)
    sound_mix.delete()
    response = HttpResponse("")
    response["HX-Trigger"] = json.dumps({"mix-deleted": {"mixId": mix_id}})
    return response


def library_swap(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")

    qs = Sound.objects.select_related("artist").prefetch_related("tags")
    collection_size = qs.count()
    sounds = serialize_sounds(qs.order_by("?")[:5], user=request.user)
    return render(
        request,
        "library/index.html#swap_view",
        {"sounds": sounds, "collection_size": collection_size},
    )


def library_search(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")

    qs = Sound.objects.select_related("artist").prefetch_related("tags")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = (
            qs.filter(
                Q(title__icontains=q)
                | Q(artist__name__icontains=q)
                | Q(tags__name__icontains=q)
            )
            .distinct()
            .order_by("title")[:20]
        )
    else:
        qs = qs.order_by("?")[:5]
    return render(
        request,
        "library/index.html#swap_list_items",
        {"sounds": serialize_sounds(qs, user=request.user)},
    )


def library_carousel(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")
    return render(request, "library/index.html#default_view")
