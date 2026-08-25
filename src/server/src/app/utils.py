import hashlib

from core.utils import get_random_avatar_url


# Stable placeholder bios, picked deterministically from the artist's name so a
# given artist always shows the same line.
EMPTY_BIOS = [
    "No story to be told here.",
    "This one lets the sound do the talking.",
    "An enigma — no words, only waveforms.",
    "Some artists write essays. This one writes silence.",
    "They left this page blank on purpose.",
    "No bio yet. The mystery is part of the mix.",
    "Still composing the story. Listen in the meantime.",
]


def _seed_hash(seed) -> str:
    """Deterministic hex digest for a seed string (stable across processes)."""
    text = (str(seed) if seed is not None else "").strip() or "cosound"
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def gradient_from_seed(seed) -> str:
    """A deterministic CSS linear-gradient derived from the seed (e.g. a name).

    Used as a placeholder cover photo when an artist has no cover image.
    """
    h = _seed_hash(seed)
    c1 = h[0:6]
    c2 = h[14:20]
    angle = int(h[6:8], 16) % 360
    return f"linear-gradient({angle}deg, #{c1}, #{c2})"


def placeholder_bio(seed) -> str:
    """A deterministic placeholder bio passage for an artist with no bio."""
    idx = int(_seed_hash(seed), 16) % len(EMPTY_BIOS)
    return EMPTY_BIOS[idx]


def build_artist_context(user, artist, fallback_name=""):
    """Build the template context for the artist_details modal.

    `artist` may be None (e.g. a sound with no linked Artist); in that case we
    fall back to the supplied name with empty stats and generated placeholders.
    """
    name = (artist.name if artist else fallback_name) or "Unknown Artist"

    # Seed placeholders by pk when we have one (so it survives a rename), else
    # by name. get_random_avatar_url expects a numeric seed.
    if artist is not None:
        seed = artist.pk
        bio = artist.bio.strip() if artist.bio else ""
        url = artist.url or ""
        avatar_url = artist.avatar.url if artist.avatar else get_random_avatar_url(seed)
        cover_url = artist.cover.url if artist.cover else ""
        uploaded_count = artist.sounds.count()
    else:
        seed = int(_seed_hash(name)[:8], 16)
        bio = ""
        url = ""
        avatar_url = get_random_avatar_url(seed)
        cover_url = ""
        uploaded_count = 0

    favourited_count = 0
    if user and user.is_authenticated and artist is not None:
        from core.models import Listener

        listener = Listener.objects.filter(user=user).first()
        if listener is not None:
            favourited_count = listener.collection.filter(artist=artist).count()

    return {
        "name": name,
        "bio": bio or placeholder_bio(name),
        "url": url,
        "avatar_url": avatar_url,
        "cover_url": cover_url,
        "cover_style": gradient_from_seed(name),
        "uploaded_count": uploaded_count,
        "favourited_count": favourited_count,
    }

def serialize_mix(sm):
    layers = []
    for sl in sm.cosound.soundlayer_set.all():
        gain = float(sl.gain)
        layers.append(
            {
                **sl.sound.asLayer(with_gain=gain),
                "artwork_url": sl.sound.art.url if sl.sound.art else "",
                "mute": False,
                "isolated": False,
                "saved": True,
                "flavor": sl.sound.flavor or "",
                "tags": " / ".join(sl.sound.tags.names()) or "Unknown",
                "gain": int(round(gain * 100)),
            }
        )
    return {
        "id": sm.id,
        "cosound_id": sm.cosound_id,
        "title": sm.title,
        "created_at": sm.created_at.isoformat(),
        "layers": layers,
    }
