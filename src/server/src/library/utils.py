import json
from urllib.parse import quote

from django.db.models import Q


def parse_layers(raw):
    """Split a posted mix into its raw form and the (sound_id, gain) pairs.

    Layers whose id is not a Sound's are dropped rather than raised on. Two
    kinds never have one: a blank layer the `+` button made room for, and a
    track from the artist's own machine — both carry a browser-made string id.
    The save buttons leave blank layers out of the post and refuse a mix holding
    a local track, so this is the backstop for a request that arrives anyway,
    and it answers "no layers" instead of a 500. That answer is also what a mix
    of nothing but blank layers gets, which is the same thing the buttons say.
    """
    try:
        layer_data = json.loads(raw or "[]")
    except (json.JSONDecodeError, ValueError):
        return None, None
    layers = []
    for layer in layer_data:
        try:
            sound_id = int(layer["sound_id"])
        except (KeyError, TypeError, ValueError):
            continue
        gain = max(0.0, min(1.0, float(layer.get("sound_gain", 1.0))))
        layers.append((sound_id, gain))
    return layer_data, layers


def generate_sound_artwork(sound):
    return "https://picsum.photos/seed/{}/400/400".format(sound.id)


# The face of a tag button in the sound picker. DiceBear's `waves` draws a
# square of white waves over one saturated dark colour, chosen from the seed —
# so a tag looks the same every time it is offered, and no two adjacent tiles
# look alike. Square and fully opaque, which is what lets the tile crop it with
# `bg-cover` and put white text straight on top.
def generate_tag_artwork(name):
    return "https://api.dicebear.com/10.x/waves/svg?seed={}".format(
        quote(name, safe="")
    )


# Defaults retained for callers that explicitly request a generated random mix.
# The library tab itself now starts from get_empty_layer instead.
OPENING_LAYERS = 3
OPENING_AUDIBLE_LAYERS = 2


def get_empty_layer():
    """Return the blank layer the library opens on."""
    return {
        "sound_id": "draft-1",
        "sound_file": "",
        "sound_title": "",
        "sound_artist": "",
        # Blank, so there is no artist and no page of theirs to reach. Spelled
        # out rather than left off because this dict stands in for one built by
        # Sound.asLayer, and the carousel reads the field on every layer.
        "artist_url": "",
        "artwork_url": "",
        "gain": 50,
        "mute": False,
        "saved": False,
        "flavor": "In the begining there was darkness.",
        "tags": "Void",
        "is_local": True,
        "is_draft": True,
    }


def get_random_sounds(user=None):
    import random
    from core.models import Listener, Sound

    saved_ids = set()
    if user and user.is_authenticated:
        try:
            saved_ids = set(
                Listener.objects.get(user=user).collection.values_list("id", flat=True)
            )
        except Listener.DoesNotExist:
            pass

    sound_ids = list(
        Sound.objects.order_by("?")[:OPENING_LAYERS].values_list("id", flat=True)
    )
    sounds = [
        {
            **sound.asLayer(with_gain=round(random.uniform(0.1, 0.9), 2)),
            "artwork_url": sound.art.url if sound.art else "",
            "mute": False,
            "saved": sound.pk in saved_ids,
            "flavor": sound.flavor or "",
            "tags": " / ".join(sound.tags.names()) or "Unknown",
        }
        for sound in Sound.objects.filter(id__in=sound_ids).prefetch_related("tags")
    ]
    for sound in sounds[OPENING_AUDIBLE_LAYERS:]:
        sound["mute"] = True
    return sounds


def serialize_sounds(sounds, user=None):
    saved_ids = set()
    if user and user.is_authenticated:
        try:
            from core.models import Listener

            saved_ids = set(
                Listener.objects.get(user=user).collection.values_list("id", flat=True)
            )
        except Listener.DoesNotExist:
            pass

    return [
        {
            **sound.asLayer(with_gain=0.5),
            "gain": 50,
            "mute": False,
            "saved": sound.pk in saved_ids,
            "flavor": sound.flavor or "",
            "tags": " / ".join(sound.tags.names()) if hasattr(sound, "tags") else "Unknown",
            "artwork_url": sound.art.url if sound.art else generate_sound_artwork(sound),
            "id": sound.id,
            "title": sound.title,
            "artist": sound.artist_name,
            "artist_name": sound.artist_name,
        }
        for sound in sounds
    ]


# How many tag buttons the picker offers. The grid is two across, so this is
# six rows — enough to scroll a little, few enough that the most-used tags are
# still the ones on screen when it opens.
PICKER_TAG_LIMIT = 12


def collected_sounds(user):
    """The sounds a listener has put their name to.

    Two things count as collected, because both are things the listener chose:
    a sound they kept with the heart, and a sound sitting in one of their saved
    mixes. Anonymous visitors have collected nothing.
    """
    from core.models import Sound

    if user is None or not user.is_authenticated:
        return Sound.objects.none()
    return Sound.objects.filter(
        Q(saved_by__user=user) | Q(soundlayer__mix__soundmix__creator=user)
    ).distinct()


def picker_tag_facets(user, limit=PICKER_TAG_LIMIT):
    """The tag buttons the sound picker opens on.

    The *names* come from what this listener has collected — the picker starts
    from the vocabulary they already use rather than the whole catalogue's. The
    *counts* are catalogue-wide, because that is what pressing the button then
    searches: the picker is how a new sound gets into a mix, so narrowing it to
    sounds already collected would make it impossible to add anything new. A
    listener with nothing collected yet (or no tags on it) gets the catalogue's
    most-used tags instead of an empty screen.
    """
    from django.contrib.contenttypes.models import ContentType
    from django.db.models import Count
    from taggit.models import Tag

    from core.models import Sound

    content_type = ContentType.objects.get_for_model(Sound)
    tagged = Tag.objects.filter(taggit_taggeditem_items__content_type=content_type)

    collected_ids = list(collected_sounds(user).values_list("id", flat=True))
    if collected_ids:
        names = Tag.objects.filter(
            taggit_taggeditem_items__content_type=content_type,
            taggit_taggeditem_items__object_id__in=collected_ids,
        ).values_list("name", flat=True)
        mine = tagged.filter(name__in=list(names))
        # `mine` is empty when the collected sounds carry no tags at all; fall
        # through to the catalogue rather than opening on nothing.
        if mine.exists():
            tagged = mine

    # Filtering the join before annotating means Count reuses it, so this is
    # "sounds carrying this tag", not "tagged rows of every kind".
    facets = tagged.annotate(sound_count=Count("taggit_taggeditem_items")).order_by(
        "-sound_count", "name"
    )[:limit]
    return [
        {
            "name": tag.name,
            "count": tag.sound_count,
            "art": generate_tag_artwork(tag.name),
        }
        for tag in facets
    ]
