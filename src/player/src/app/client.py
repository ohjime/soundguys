import json
import os
import urllib.error
import urllib.request

import wget

ROOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
CONFIG_PATH = os.path.join(ROOT_DIR, "cosound.json")
PLAY_SCRIPT = os.path.join(ROOT_DIR, "bin/play")
REFRESH_INTERVAL = 120  # In Seconds
CROSSFADE_INTERVAL = 10  # In Seconds
TMP_STREAM_DIR = "/tmp/cosound"

API_BASE_URL = (
    os.environ.get("COSOUND_API_URL", "http://localhost:8000/api").strip().rstrip("/")
)
HTTP_TIMEOUT = 15  # Bound a failed request so later refreshes can still run.


def _api_get(path: str, api_key: str) -> dict:
    request = urllib.request.Request(
        f"{API_BASE_URL}{path}",
        headers={"X-API-Key": api_key},
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def get_latest_manifest(api_key: str) -> dict:
    """Fetch the player's sound library: {sound_id: remote_url}."""
    return _api_get("/manifest", api_key)


def get_latest_cosound(api_key: str) -> dict:
    """Fetch the player's latest cosound: {sound_id: gain}."""
    return _api_get("/cosound", api_key)


def get_player_info(api_key: str) -> dict:
    """Fetch player details and currently playing layers from /player.

    Returns {name, manager, location, layers: [{sound_id, title, artist, gain}]}.
    Falls back to /cosound if the server does not expose /player yet.
    """
    try:
        return _api_get("/player", api_key)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        cosound = get_latest_cosound(api_key)
        return {
            "name": "",
            "manager": "",
            "location": "",
            "layers": [
                {
                    "sound_id": int(sound_id),
                    "title": f"Sound {sound_id}",
                    "artist": "",
                    "gain": gain,
                }
                for sound_id, gain in cosound.items()
            ],
        }


def get_sound(sound_id, remote_path) -> str:
    # First check if sound_id exists locally:
    os.makedirs(ASSETS_DIR, exist_ok=True)
    local_path = os.path.join(ASSETS_DIR, str(sound_id))
    # Otherwise download from remote url to local path
    if not os.path.exists(local_path):
        wget.download(remote_path, local_path)
    return local_path
