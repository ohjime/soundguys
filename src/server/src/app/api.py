from ninja import NinjaAPI
from ninja.security import APIKeyHeader
from ninja.throttling import AuthRateThrottle

from core.models import Player, Sound

api = NinjaAPI()


class PlayerTokenAuth(APIKeyHeader):
    param_name = "X-API-Key"

    def authenticate(self, request, key):
        try:
            return Player.objects.select_related("post").get(token=key)
        except Player.DoesNotExist:
            return None


@api.get(
    "/manifest",
    auth=PlayerTokenAuth(),
    throttle=[AuthRateThrottle("10/m")],
)
def get_manifest(request) -> dict[str, str]:
    """Return the player's sound library as {sound_id: remote_url}."""
    player: Player = request.auth
    return {
        str(sound.pk): request.build_absolute_uri(sound.file.url)
        for sound in player.post.collection.all()
        if sound.file
    }


@api.get(
    "/cosound",
    auth=PlayerTokenAuth(),
    throttle=[AuthRateThrottle("10/m")],
)
def get_cosound(request) -> dict[str, float]:
    """Return the player's latest cosound as {sound_id: gain}."""
    player: Player = request.auth
    return {
        str(layer.sound_id): layer.sound_gain
        for layer in player.playing.layers
    }


@api.get(
    "/player",
    auth=PlayerTokenAuth(),
    throttle=[AuthRateThrottle("10/m")],
)
def get_player(request) -> dict:
    """Return player details and the currently playing cosound layers."""
    player: Player = request.auth
    sounds = Sound.objects.in_bulk(
        [layer.sound_id for layer in player.playing.layers]
    )
    return {
        "name": player.name,
        "manager": player.manager.name,
        "location": player.location,
        "layers": [
            {
                "sound_id": layer.sound_id,
                "title": (
                    sounds[layer.sound_id].title
                    if layer.sound_id in sounds
                    else f"Sound {layer.sound_id}"
                ),
                "artist": (
                    sounds[layer.sound_id].artist_name
                    if layer.sound_id in sounds
                    else ""
                ),
                "gain": layer.sound_gain,
            }
            for layer in player.playing.layers
        ],
    }


# Resolve the NinjaAPI's URLs exactly once. django-ninja refuses to attach the
# same NinjaAPI instance twice (ConfigError on a duplicate namespace), so both
# mount points — "/api/" in config.urls and "/" in config.urls_api (the
# api.cosound.ca subdomain) — must reuse this single tuple.
api_urls = api.urls

