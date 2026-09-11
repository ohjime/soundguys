import asyncio
import json
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

from textual.widgets import Static
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedError, InvalidStatus
from websockets.frames import Close
from websockets.http11 import Response

from app import client, live, tui


READY = json.dumps({"type": "player.ready", "schema_version": 1})
CHANGED = json.dumps({"type": "player.changed", "schema_version": 1})


class WebSocketURLTests(unittest.TestCase):
    def test_default_endpoint_uses_api_origin(self):
        for api_url, expected in (
            ("http://localhost:8000/api", "ws://localhost:8000/ws/player/"),
            ("http://localhost:8000/api/", "ws://localhost:8000/ws/player/"),
            ("https://api.cosound.ca", "wss://api.cosound.ca/ws/player/"),
            ("https://example.com/prefix/api", "wss://example.com/ws/player/"),
            ("http://[::1]:8000/api", "ws://[::1]:8000/ws/player/"),
        ):
            with self.subTest(api_url=api_url):
                self.assertEqual(live.build_websocket_url(api_url), expected)

    def test_override_is_explicit_and_preserves_its_endpoint(self):
        self.assertEqual(
            live.build_websocket_url("https://api.example", "wss://live.example/custom/"),
            "wss://live.example/custom/",
        )

    def test_invalid_urls_cannot_receive_an_api_key(self):
        for url in (
            "https://example.com/ws/player/",
            "ws:///ws/player/",
            "ws://example.com:99999/ws/player/",
            "ws://user:secret@example.com/ws/player/",
            "ws://example.com/ws/player/?token=secret",
            "ws://example.com/ws/player/#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                live.build_websocket_url("http://localhost/api", url)
        with self.assertRaises(ValueError):
            live.build_websocket_url("ftp://example.com/api")

    def test_http_requests_have_bounded_timeout_and_header_auth(self):
        response = Mock()
        response.read.return_value = b'{"layers": []}'
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        with patch.object(client.urllib.request, "urlopen", return_value=context) as fetch:
            self.assertEqual(client._api_get("/player", "player-secret"), {"layers": []})
        request = fetch.call_args.args[0]
        self.assertEqual(request.get_header("X-api-key"), "player-secret")
        self.assertNotIn("player-secret", request.full_url)
        self.assertEqual(fetch.call_args.kwargs["timeout"], client.HTTP_TIMEOUT)


class SocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_and_changes_refresh_on_event_loop_and_cancel_closes_socket(self):
        connected = asyncio.Event()
        allow_ready = asyncio.Event()
        refreshed = asyncio.Event()
        closed = asyncio.Event()
        refresh = Mock(side_effect=refreshed.set)
        status = Mock()
        requests = []

        async def handler(websocket):
            requests.append(websocket.request)
            connected.set()
            # Ignore pre-subscription notifications. The ready resync covers them.
            await websocket.send(CHANGED)
            await allow_ready.wait()
            await websocket.send(READY)
            await websocket.send('{"type":"player.changed","schema_version":2}')
            await websocket.send("not json")
            await websocket.send("[]")
            await websocket.send(CHANGED)
            await websocket.wait_closed()
            closed.set()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            task = asyncio.create_task(
                live.watch_player_changes(
                    "secret", refresh, status,
                    url=f"ws://127.0.0.1:{port}/ws/player/",
                )
            )
            try:
                await asyncio.wait_for(connected.wait(), 2)
                refresh.assert_not_called()
                status.assert_called_once_with("Connecting…")
                allow_ready.set()
                await asyncio.wait_for(refreshed.wait(), 2)
                # Yield until the second valid notification has been processed.
                async with asyncio.timeout(2):
                    while refresh.call_count < 2:
                        await asyncio.sleep(0.01)
                self.assertEqual(refresh.call_count, 2)
                self.assertEqual(requests[0].path, "/ws/player/")
                self.assertEqual(requests[0].headers["X-API-Key"], "secret")
                status.assert_any_call("Connected")
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            await asyncio.wait_for(closed.wait(), 2)

    async def test_clean_and_abrupt_disconnects_back_off_and_resync(self):
        refresh = Mock()
        status = Mock()
        attempts = 0

        class Socket:
            async def recv(self):
                return READY

            async def __aiter__(self):
                if attempts == 2:
                    raise ConnectionClosedError(Close(1011, "restart"), None)
                if False:
                    yield

        @asynccontextmanager
        async def connect(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            yield Socket()

        sleep = AsyncMock(side_effect=[None, None, asyncio.CancelledError()])
        with (
            patch.object(live, "_PlayerConnection", side_effect=connect) as connector,
            patch.object(live.asyncio, "sleep", sleep),
            patch.object(live.random, "uniform", side_effect=lambda low, high: high),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await live.watch_player_changes(
                    "secret", refresh, status, url="ws://localhost/ws/player/"
                )
        self.assertEqual(refresh.call_count, 3)  # Each newly subscribed socket resyncs.
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4])
        self.assertEqual(connector.call_args.kwargs["additional_headers"], {"X-API-Key": "secret"})
        self.assertEqual(connector.call_args.kwargs["ping_timeout"], 20)

    async def test_failed_handshakes_back_off_and_auth_denials_retry_slowly(self):
        status = Mock()
        for failure, delay in (
            (OSError("offline"), 1),
            (InvalidStatus(Response(403, "Forbidden", Headers())), 60),
            (ConnectionClosedError(Close(4401, "unauthorized"), None), 60),
        ):
            @asynccontextmanager
            async def connect(*args, **kwargs):
                raise failure
                yield

            with (
                self.subTest(failure=type(failure).__name__),
                patch.object(live, "_PlayerConnection", side_effect=connect),
                patch.object(live.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError())) as sleep,
                patch.object(live.random, "uniform", side_effect=lambda low, high: high),
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await live.watch_player_changes(
                        "secret", Mock(), status, url="ws://localhost/ws/player/"
                    )
                sleep.assert_awaited_once_with(delay)
        status.assert_any_call("Authorization failed (polling active)")

    async def test_ready_timeout_retries_without_premature_refresh(self):
        refresh = Mock()

        class Socket:
            async def recv(self):
                await asyncio.Event().wait()

        @asynccontextmanager
        async def connect(*args, **kwargs):
            yield Socket()

        with (
            patch.object(live, "_PlayerConnection", side_effect=connect),
            patch.object(live, "READY_TIMEOUT", 0.01),
            patch.object(live.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError())),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await live.watch_player_changes(
                    "secret", refresh, Mock(), url="ws://localhost/ws/player/"
                )
        refresh.assert_not_called()

    async def test_redirect_does_not_forward_credentials(self):
        destination = Mock()

        async def destination_handler(websocket):
            destination(websocket.request.headers)

        async with serve(destination_handler, "127.0.0.1", 0) as other_server:
            port = other_server.sockets[0].getsockname()[1]

            async def redirect(connection, request):
                return Response(
                    302, "Found", Headers({"Location": f"ws://127.0.0.1:{port}/"})
                )

            async with serve(destination_handler, "127.0.0.1", 0, process_request=redirect) as server:
                origin_port = server.sockets[0].getsockname()[1]
                with self.assertRaises(InvalidStatus):
                    async with live._PlayerConnection(
                        f"ws://127.0.0.1:{origin_port}/ws/player/",
                        additional_headers={"X-API-Key": "secret"},
                    ):
                        self.fail("Redirect should have been rejected")
        destination.assert_not_called()


class PlayerUIUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_updates_without_audio_transition_or_new_history(self):
        player = Mock(master_gain=0.7, fs=48_000, channels=2, device_info={"name": "Test"})
        player.get_levels.return_value = {}
        app = tui.CosoundPlayerApp("key", {"1": "/conditioned/1.wav"}, player)
        before = {
            "name": "Hall", "manager": "Manager", "sleeping": False,
            "layers": [{"sound_id": 1, "gain": 0.5, "title": "Old title", "artist": "Old artist"}],
        }
        after = {
            "name": "", "manager": "", "sleeping": True,
            "location": "New location", "collection": [1, 2],
            "layers": [{"sound_id": 1, "gain": 0.5, "title": "New title", "artist": "New artist"}],
        }
        with (
            patch.object(app, "_watch_live_updates"),
            patch.object(app, "_refresh_cosound_worker"),
            patch.object(tui, "get_player_info", side_effect=[before, after]),
        ):
            async with app.run_test(size=(140, 45)) as pilot:
                await asyncio.to_thread(app._run_refresh)
                await pilot.pause()
                entry = app._current_entry
                await asyncio.to_thread(app._run_refresh)
                await pilot.pause()

                self.assertEqual(app.player_info, after)
                self.assertIs(app._current_entry, entry)
                self.assertEqual(len(app.query(tui.CosoundEntry)), 1)
                self.assertEqual(str(app.query_one("#player-name", Static).content), "PLAYER: —")
                self.assertEqual(str(app.query_one("#managed-by", Static).content), "MANAGED BY: —")
                self.assertEqual(str(app.query_one(".layer-title", Static).content), "New title — New artist")

                blank = {**after, "layers": [{"sound_id": 1, "gain": 0.5, "title": "", "artist": ""}]}
                app._apply_state(blank, False)
                self.assertEqual(str(app.query_one(".layer-title", Static).content), "Sound 1")
                app._show_live_status("Reconnecting… (polling active)")
                self.assertIn("RECONNECTING", str(app.query_one("#live-status", Static).content))

        player.queue_sound.assert_called_once_with("/conditioned/1.wav", 0.5)
        player.dequeue_cosound.assert_called_once_with()

    async def test_textual_worker_triggers_coalesced_refresh_and_cancels_on_exit(self):
        player = Mock(master_gain=0.7, fs=48_000, channels=2, device_info={"name": "Test"})
        player.get_levels.return_value = {}
        app = tui.CosoundPlayerApp("key", {}, player)
        listening = asyncio.Event()
        cancelled = asyncio.Event()

        async def listen(api_key, refresh, status):
            self.assertEqual(api_key, "key")
            status("Connected")
            refresh()
            refresh()
            listening.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with (
            patch.object(tui, "watch_player_changes", side_effect=listen),
            patch.object(app, "_refresh_cosound_worker") as start_refresh,
        ):
            async with app.run_test(size=(140, 45)):
                await asyncio.wait_for(listening.wait(), 2)
                self.assertEqual(app._refresh_generation, 3)
                start_refresh.assert_called_once_with()
                self.assertIn("CONNECTED", str(app.query_one("#live-status", Static).content))
        self.assertTrue(cancelled.is_set())


if __name__ == "__main__":
    unittest.main()
