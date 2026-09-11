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
from library.utils import parse_layers, picker_tag_facets, serialize_sounds


def library_save(request):

    if not request.htmx:
        return HttpResponse("Request Denied.")

    if not request.user.is_authenticated:
        response = login_modal(request)
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
            # Only a mix that is already a row has a time to show. A name
            # carried in from an edit or an Explore post is a suggestion, not a
            # record of anything, so the dialog says nothing about when.
            "existing_saved_at": existing.updated_at if existing else None,
        },
    )


def library_save_confirm(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")

    if not request.user.is_authenticated:
        return HttpResponse("Request Denied.", status=401)

    layer_data, layers = parse_layers(request.POST.get("layers"))
    if not layers:
        return HttpResponse("No layers provided.", status=400)

    title = (request.POST.get("title") or "").strip()

    cosound = Cosound.get_or_create_from_layers(layers)

    # A name is how a listener finds a Cosound again, so two of theirs cannot
    # share one. Re-saving the same layers is not a clash — that is the same
    # Cosound answering to the same name, and the write below is a rename at
    # most. A clash is a *different* Cosound under this name: keeping the name
    # after tweaking a loaded mix, which is the ordinary way to edit one. That
    # loses the version saved before, so it is asked about before it is done
    # rather than reported afterwards.
    clash = (
        SoundMix.objects.filter(creator=request.user, title__iexact=title)
        .exclude(cosound=cosound)
        .first()
        if title
        else None
    )
    if clash and request.POST.get("overwrite") != "1":
        return show_modal(
            request,
            "app/home.html#mix_overwrite_modal",
            {
                "layers_json": json.dumps(layer_data),
                "title": title,
                "overwritten_saved_at": clash.updated_at,
            },
        )

    sound_mix, created = SoundMix.objects.get_or_create(
        creator=request.user, cosound=cosound
    )
    sound_mix.title = title
    sound_mix.save(update_fields=["title", "updated_at"])

    # The confirmed overwrite. The new Cosound has the name now, so the old row
    # goes; the deck drops its entry on `mix-deleted`, the same event the delete
    # button fires.
    overwritten_id = None
    if clash:
        overwritten_id = clash.id
        clash.delete()

    # `mix-titled` is what the mix answers to from here on: the transport hands
    # this name back to the save dialog next time, so a rename does not leave
    # the old title waiting in the box.
    triggers = {"close-modal": True, "mix-titled": {"title": sound_mix.title}}
    if overwritten_id is not None:
        triggers["mix-deleted"] = {"mixId": overwritten_id}
    if created:
        triggers["mix-saved"] = {"mix": serialize_mix(sound_mix)}

    response = close_modal(request)
    response["HX-Trigger"] = json.dumps(triggers)
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
    """Open the sound picker over the card.

    It opens on tags rather than on a handful of sounds. A blank layer is a
    question about what kind of sound belongs there, and a name for that — Rain,
    Traffic, Voices — narrows it far faster than scrolling an unfiltered list
    does. The sounds arrive once the listener has said which kind they want,
    either by pressing a tag or by typing.
    """
    if not request.htmx:
        return HttpResponse("Request Denied.")

    return render(
        request,
        "library/index.html#swap_view",
        {
            "tags": picker_tag_facets(request.user),
            "collection_size": Sound.objects.count(),
        },
    )


def library_search(request):
    """The picker's results pane, for both ways of narrowing it.

    `tag` is the button the listener pressed (and the radio it checks in the
    filter above the search box); `q` is what they typed. They compose, so
    typing inside a tag keeps searching within it. With neither, there is
    nothing to show a list of — that is the opening state, and the tag buttons
    come back.
    """
    if not request.htmx:
        return HttpResponse("Request Denied.")

    q = (request.GET.get("q") or "").strip()
    tag = (request.GET.get("tag") or "").strip()

    if not q and not tag:
        return render(
            request,
            "library/index.html#swap_tags",
            {"tags": picker_tag_facets(request.user)},
        )

    qs = Sound.objects.select_related("artist").prefetch_related("tags")
    if tag:
        qs = qs.filter(tags__name=tag)
    if q:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(artist__name__icontains=q)
            | Q(tags__name__icontains=q)
        )
    qs = qs.distinct().order_by("title")[:20]
    return render(
        request,
        "library/index.html#swap_results",
        {"sounds": serialize_sounds(qs, user=request.user)},
    )


def library_carousel(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")
    return render(request, "library/index.html#default_view")
