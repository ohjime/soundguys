import asyncio
from contextlib import asynccontextmanager
from unittest.mock import patch

from asgiref.testing import ApplicationCommunicator
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from redis.exceptions import ConnectionError as RedisConnectionError

from app.api import PlayerRateThrottle, get_player
from config.asgi import application
from core.models import Manager, Player, Prediction, Sound, User
from core.player_events import player_group_name


@asynccontextmanager
async def socket_connection(token=None, host="localhost", query=b"", extra_headers=()):
    headers = [(b"host", host.encode()), *extra_headers]
    if token is not None:
        headers.append((b"x-api-key", token.encode()))
    socket = ApplicationCommunicator(application, {
        "type": "websocket", "path": "/ws/player/", "headers": headers,
        "query_string": query, "subprotocols": [],
    })
    await socket.send_input({"type": "websocket.connect"})
    try:
        yield socket
    finally:
        await socket.send_input({"type": "websocket.disconnect", "code": 1000})
        await socket.wait()


@override_settings(
    ALLOWED_HOSTS=["localhost", ".cosound.ca"],
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class PlayerSocketTests(TransactionTestCase):
    def setUp(self):
        self.publish = patch("core.player_events.publish_player_changes").start()
        self.addCleanup(patch.stopall)
        user = User.objects.create_user(username="socket-manager")
        manager = Manager.objects.create(user=user, name="Manager")
        self.player = Player.objects.create(manager=manager, name="Player")
        self.other = Player.objects.create(manager=manager, name="Other")

    async def assert_ready(self, socket):
        self.assertEqual(await socket.receive_output(), {"type": "websocket.accept", "subprotocol": None})
        message = await socket.receive_output()
        self.assertEqual(message["type"], "websocket.send")
        self.assertJSONEqual(message["text"], {"type": "player.ready", "schema_version": 1})

    async def test_missing_invalid_duplicate_and_query_credentials_are_rejected(self):
        for token, query, extra in [
            (None, b"", ()), ("invalid", b"", ()), ("bad\x00token", b"", ()),
            (None, f"token={self.player.token}".encode(), ()),
            (self.player.token, b"", ((b"x-api-key", b"another"),)),
        ]:
            async with socket_connection(token, query=query, extra_headers=extra) as socket:
                self.assertEqual(await socket.receive_output(), {"type": "websocket.close", "code": 4401})

    async def test_untrusted_host_is_rejected(self):
        async with socket_connection(self.player.token, host="attacker.example") as socket:
            self.assertEqual(await socket.receive_output(), {"type": "websocket.close", "code": 4403})

    async def test_ready_follows_subscription_and_only_own_events_are_delivered(self):
        async with socket_connection(self.player.token, host="api.cosound.ca") as socket:
            await self.assert_ready(socket)
            layer = get_channel_layer()
            self.assertTrue(layer.groups[player_group_name(self.player.pk)])
            await layer.group_send(player_group_name(self.other.pk), {"type": "player.changed"})
            self.assertTrue(await socket.receive_nothing(timeout=0.03))
            await layer.group_send(player_group_name(self.player.pk), {
                "type": "player.changed", "token": "never-forward-this",
            })
            message = await socket.receive_output()
            self.assertJSONEqual(message["text"], {"type": "player.changed", "schema_version": 1})
        self.assertFalse(layer.groups.get(player_group_name(self.player.pk)))

    async def test_rotation_revokes_existing_subscription(self):
        async with socket_connection(self.player.token) as socket:
            await self.assert_ready(socket)
            await database_sync_to_async(Player.objects.filter(pk=self.player.pk).update)(token="rotated")
            await get_channel_layer().group_send(player_group_name(self.player.pk), {"type": "player.changed"})
            self.assertEqual(await socket.receive_output(), {"type": "websocket.close", "code": 4401})

    async def test_idle_subscription_is_renewed_and_rechecks_credentials(self):
        with patch("app.consumers.SUBSCRIPTION_REFRESH_SECONDS", 0.02):
            async with socket_connection(self.player.token) as socket:
                await self.assert_ready(socket)
                layer = get_channel_layer()
                layer.groups.clear()
                # Wait on a condition, bounded by the test's normal timeout.
                async with asyncio.timeout(1):
                    while not layer.groups.get(player_group_name(self.player.pk)):
                        await asyncio.sleep(0.01)
                await database_sync_to_async(Player.objects.filter(pk=self.player.pk).update)(token="rotated")
                self.assertEqual(await socket.receive_output(), {"type": "websocket.close", "code": 4401})

    async def test_reconnect_sends_new_ready_and_rejects_client_commands(self):
        for _ in range(2):
            async with socket_connection(self.player.token) as socket:
                await self.assert_ready(socket)
                await socket.send_input({"type": "websocket.receive", "text": '{"type":"subscribe","player_id":999}'})
                self.assertEqual(await socket.receive_output(), {"type": "websocket.close", "code": 1008})

    async def test_redis_unavailable_does_not_acknowledge_subscription(self):
        with patch("app.consumers.PlayerConsumer._subscribe", side_effect=OSError("offline")):
            with self.assertLogs("app.consumers", level="WARNING"):
                async with socket_connection(self.player.token) as socket:
                    self.assertEqual(await socket.receive_output(), {"type": "websocket.close", "code": 1013})

    async def test_redis_receive_failure_closes_without_leaking_cleanup_errors(self):
        fail_receive = asyncio.Event()

        async def unavailable_channel(channel_name):
            await fail_receive.wait()
            raise RedisConnectionError("offline")

        with patch.object(get_channel_layer(), "receive", side_effect=unavailable_channel):
            with self.assertLogs("app.consumers", level="WARNING"):
                async with socket_connection(self.player.token) as socket:
                    await self.assert_ready(socket)
                    fail_receive.set()
                    self.assertEqual(await socket.receive_output(), {"type": "websocket.close", "code": 1013})


class PlayerStateTests(TestCase):
    def test_snapshot_includes_playback_and_metadata_without_credentials(self):
        user = User.objects.create_user(username="state-manager")
        manager = Manager.objects.create(user=user, name="Manager")
        sound = Sound.objects.create(title="Rain", embeddings=[0.0] * 5)
        playing = Prediction.new()
        playing.add_layer(sound.pk, 0.5)
        player = Player.objects.create(manager=manager, name="Player", bio="Room sounds", location="Library", playing=playing)
        request = RequestFactory().get("/api/player")
        request.auth = player
        state = get_player(request)
        self.assertFalse(state["sleeping"])
        self.assertEqual(state["bio"], "Room sounds")
        self.assertEqual(state["post_id"], player.post_id)
        self.assertEqual(state["layers"][0]["gain"], 0.5)
        self.assertNotIn("token", state)
        self.assertNotIn(player.token, str(state))
        player.update(Prediction.new())
        self.assertTrue(get_player(request)["sleeping"])

    def test_rate_limit_uses_stable_player_id(self):
        throttle = PlayerRateThrottle("120/m")
        request = RequestFactory().get("/api/player")
        request.auth = Player(pk=1, name="Player")
        key = throttle.get_cache_key(request)
        request.auth.name = "Renamed"
        self.assertEqual(throttle.get_cache_key(request), key)
        request.auth.pk = 2
        self.assertNotEqual(throttle.get_cache_key(request), key)
