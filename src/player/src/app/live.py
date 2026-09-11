"""Authenticated change notifications; HTTP remains the source of player state."""

import asyncio
import json
import os
import random
from collections import deque
from collections.abc import Callable
from time import monotonic
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from app.client import API_BASE_URL

READY_TIMEOUT = 15
MAX_RETRY_DELAY = 60
STABLE_CONNECTION_SECONDS = 30


def build_websocket_url(api_url: str, override: str | None = None) -> str:
    """Use the API's origin, not its /api path, for the socket endpoint."""
    explicit = bool(override and override.strip())
    parsed = urlsplit(override.strip() if explicit else api_url.strip())
    schemes = ("ws", "wss") if explicit else ("http", "https")
    if (
        parsed.scheme not in schemes
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid COSOUND_WS_URL or COSOUND_API_URL")
    # Validate the port, including malformed and out-of-range port numbers.
    parsed.port
    if explicit:
        return urlunsplit(parsed)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, "/ws/player/", "", ""))


class _PlayerConnection(connect):
    def process_redirect(self, exc: Exception) -> Exception:
        # Never forward the custom API-key header to a redirected destination.
        # Configure COSOUND_WS_URL explicitly when a different endpoint is used.
        return exc


def _event_type(message: str | bytes) -> str | None:
    try:
        event = json.loads(message)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(event, dict) or event.get("schema_version") != 1:
        return None
    return event.get("type")


async def _receive_changes(websocket, refresh: Callable[[], None], status,
                           on_vote=None, seen_votes=None) -> None:
    if seen_votes is None:
        seen_votes = deque(maxlen=512)
    # A successful handshake isn't sufficient: the server must have joined the
    # player's group before we fetch the snapshot, or an update can be missed.
    async with asyncio.timeout(READY_TIMEOUT):
        while _event_type(await websocket.recv()) != "player.ready":
            pass
    status("Connected")
    refresh()
    async for message in websocket:
        if _event_type(message) in ("player.changed", "player.ready"):
            refresh()
        elif _event_type(message) == "player.vote_received" and on_vote is not None:
            event = json.loads(message)
            vote_id = event.get("vote_id")
            if type(vote_id) is int and vote_id > 0 and vote_id not in seen_votes:
                seen_votes.append(vote_id)
                on_vote()


async def watch_player_changes(
    api_key: str,
    refresh: Callable[[], None],
    status: Callable[[str], None],
    *,
    url: str | None = None,
    on_vote: Callable[[], None] | None = None,
) -> None:
    """Listen until cancelled, reconnecting with bounded, jittered backoff.

    Callbacks execute on this coroutine's event loop. The refresh callback must
    schedule the existing background HTTP/audio worker, never do I/O itself.
    """
    try:
        socket_url = build_websocket_url(
            API_BASE_URL, url if url is not None else os.environ.get("COSOUND_WS_URL")
        )
    except ValueError:
        status("Check connection URL (polling active)")
        return

    status("Connecting…")
    retry_delay = 1.0
    seen_votes = deque(maxlen=512)
    while True:
        started_at = monotonic()
        denied = False
        try:
            async with _PlayerConnection(
                socket_url,
                additional_headers={"X-API-Key": api_key},
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_size=4096,
                max_queue=16,
            ) as websocket:
                await _receive_changes(websocket, refresh, status, on_vote, seen_votes)
        except asyncio.CancelledError:
            raise
        except InvalidStatus as error:
            denied = error.response.status_code in (401, 403)
        except ConnectionClosed as error:
            denied = error.rcvd is not None and error.rcvd.code in (4401, 4403)
        except (OSError, TimeoutError, WebSocketException):
            pass

        if monotonic() - started_at >= STABLE_CONNECTION_SECONDS:
            retry_delay = 1.0
        if denied:
            status("Authorization failed (polling active)")
            delay = MAX_RETRY_DELAY
        else:
            status("Reconnecting… (polling active)")
            delay = retry_delay
        # Every closure, including a clean one, waits before reconnecting. Do not
        # let an unavailable or rejecting server cause a tight connection loop.
        await asyncio.sleep(random.uniform(delay * 0.75, delay))
        retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY)
