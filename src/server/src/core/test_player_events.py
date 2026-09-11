import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.db import transaction
from django.db.models.signals import post_save
from django.test import SimpleTestCase, TestCase, override_settings

from core.models import Artist, Manager, Player, Prediction, Sound, User
from core.player_events import notify_players_changed, publish_player_changes


class PlayerPublisherTests(SimpleTestCase):
    @patch("core.player_events.get_channel_layer")
    def test_publishes_only_an_invalidation_to_each_player_group(self, get_layer):
        get_layer.return_value = SimpleNamespace(group_send=AsyncMock())

        publish_player_changes([7, 11])

        self.assertEqual(get_layer.return_value.group_send.await_count, 2)
        for player_id in (7, 11):
            get_layer.return_value.group_send.assert_any_await(
                f"player.{player_id}",
                {"type": "player.changed", "schema_version": 1},
            )

    @patch("core.player_events.get_channel_layer")
    def test_redis_failure_is_logged_without_escaping(self, get_layer):
        get_layer.return_value = SimpleNamespace(
            group_send=AsyncMock(side_effect=ConnectionError("Redis unavailable"))
        )

        with self.assertLogs("core.player_events", level="WARNING"):
            publish_player_changes([7])

    @override_settings(PLAYER_EVENT_PUBLISH_TIMEOUT=0.01)
    @patch("core.player_events.get_channel_layer")
    def test_stalled_publication_is_cancelled(self, get_layer):
        cancelled = []

        async def stalled_send(*args):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.append(True)

        get_layer.return_value = SimpleNamespace(group_send=stalled_send)
        with self.assertLogs("core.player_events", level="WARNING"):
            publish_player_changes([7])

        self.assertEqual(cancelled, [True])

    @patch("core.player_events.transaction.on_commit")
    @patch("core.player_events.publish_player_changes")
    def test_captures_unique_ids_and_uses_the_writes_database(self, publish, on_commit):
        player_ids = [7, 7, 11]
        notify_players_changed(player_ids, using="other_database")
        player_ids.append(12)

        self.assertEqual(on_commit.call_args.kwargs, {"using": "other_database"})
        publish.assert_not_called()
        on_commit.call_args.args[0]()
        publish.assert_called_once_with((7, 11))


class PlayerChangeSignalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user(
            username="player-events", email="player-events@example.com"
        )
        cls.manager = Manager.objects.create(user=user, name="First manager")
        other_manager = Manager.objects.create(user=user, name="Other manager")
        cls.artist = Artist.objects.create(name="Original artist")
        cls.sound = Sound.objects.create(
            title="Original sound",
            file="sounds/original.wav",
            artist=cls.artist,
            embeddings=[0] * 5,
        )
        cls.other_sound = Sound.objects.create(
            title="Other sound", file="sounds/other.wav", embeddings=[0] * 5
        )
        cls.collecting_player = Player.objects.create(
            name="Collection room", manager=cls.manager
        )
        cls.collecting_player.post.collection.add(cls.sound)
        prediction = Prediction()
        prediction.add_layer(cls.sound.pk)
        cls.playing_player = Player.objects.create(
            name="Playing room", manager=cls.manager, playing=prediction
        )
        cls.unrelated_player = Player.objects.create(
            name="Unrelated room", manager=other_manager
        )
        cls.unrelated_player.post.collection.add(cls.other_sound)

    def assert_notified(self, publish, player_ids):
        notified = {
            player_id
            for call in publish.call_args_list
            for player_id in call.args[0]
        }
        self.assertEqual(notified, set(player_ids))

    @patch("core.player_events.publish_player_changes")
    def test_player_save_waits_for_commit(self, publish):
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                self.collecting_player.name = "Renamed room"
                self.collecting_player.save(update_fields=["name"])
                publish.assert_not_called()
            publish.assert_not_called()

        publish.assert_called_once_with((self.collecting_player.pk,))

    @patch("core.player_events.publish_player_changes")
    def test_rolled_back_player_change_has_no_notification(self, publish):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    self.collecting_player.name = "Should roll back"
                    self.collecting_player.save(update_fields=["name"])
                    raise RuntimeError("rollback")
            except RuntimeError:
                pass

        publish.assert_not_called()
        self.collecting_player.refresh_from_db()
        self.assertEqual(self.collecting_player.name, "Collection room")

    @patch("core.player_events.publish_player_changes")
    def test_creation_and_deletion_notify_the_same_player_id(self, publish):
        with self.captureOnCommitCallbacks(execute=True):
            player = Player.objects.create(name="New room", manager=self.manager)
        player_id = player.pk
        publish.assert_called_once_with((player_id,))
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            player.delete()
        publish.assert_called_once_with((player_id,))

    @patch("core.player_events.publish_player_changes")
    def test_raw_fixture_save_does_not_notify(self, publish):
        with self.captureOnCommitCallbacks(execute=True):
            post_save.send(
                sender=Player,
                instance=self.collecting_player,
                created=False,
                raw=True,
                using="default",
                update_fields=None,
            )
        publish.assert_not_called()

    @patch("core.player_events.publish_player_changes")
    def test_prediction_update_notifies_its_player(self, publish):
        prediction = Prediction()
        prediction.add_layer(self.other_sound.pk, 0.4)
        with self.captureOnCommitCallbacks(execute=True):
            self.playing_player.update(prediction)
        publish.assert_called_once_with((self.playing_player.pk,))

    @patch("core.player_events.publish_player_changes")
    def test_collection_add_remove_and_clear_notify_the_owner(self, publish):
        collection = self.collecting_player.post.collection
        for operation in (
            lambda: collection.add(self.other_sound),
            lambda: collection.remove(self.other_sound),
            collection.clear,
        ):
            with self.subTest(operation=operation):
                publish.reset_mock()
                with self.captureOnCommitCallbacks(execute=True):
                    operation()
                publish.assert_called_once_with((self.collecting_player.pk,))

    @patch("core.player_events.publish_player_changes")
    def test_reverse_collection_changes_and_clear_find_affected_players(self, publish):
        collection = self.sound.localpost_set
        for operation, expected in (
            (lambda: collection.add(self.unrelated_player.post), [self.unrelated_player.pk]),
            (lambda: collection.remove(self.unrelated_player.post), [self.unrelated_player.pk]),
            (collection.clear, [self.collecting_player.pk]),
        ):
            with self.subTest(operation=operation):
                publish.reset_mock()
                with self.captureOnCommitCallbacks(execute=True):
                    operation()
                self.assert_notified(publish, expected)

    @patch("core.player_events.publish_player_changes")
    def test_sound_metadata_reaches_collection_and_playing_only_players(self, publish):
        for field, value in (
            ("title", "Renamed sound"),
            ("file", "sounds/replacement.wav"),
            ("artist", None),
        ):
            with self.subTest(field=field):
                publish.reset_mock()
                with self.captureOnCommitCallbacks(execute=True):
                    setattr(self.sound, field, value)
                    self.sound.save(update_fields=[field])
                self.assert_notified(
                    publish, [self.collecting_player.pk, self.playing_player.pk]
                )

    @patch("core.player_signals._player_ids_for_sounds")
    @patch("core.player_events.publish_player_changes")
    def test_embedding_only_update_does_not_search_or_notify_players(self, publish, find_players):
        with self.captureOnCommitCallbacks(execute=True):
            self.sound.embeddings = [0.1] * 5
            self.sound.save(update_fields=["embeddings"])
        find_players.assert_not_called()
        publish.assert_not_called()

    @patch("core.player_events.publish_player_changes")
    def test_sound_delete_captures_collection_before_cascade(self, publish):
        with self.captureOnCommitCallbacks(execute=True):
            self.sound.delete()
        self.assert_notified(
            publish, [self.collecting_player.pk, self.playing_player.pk]
        )

    @patch("core.player_events.publish_player_changes")
    def test_manager_name_change_notifies_only_managed_players(self, publish):
        with self.captureOnCommitCallbacks(execute=True):
            self.manager.name = "Renamed manager"
            self.manager.save(update_fields=["name"])
        self.assert_notified(
            publish, [self.collecting_player.pk, self.playing_player.pk]
        )

    @patch("core.player_events.publish_player_changes")
    def test_artist_name_change_and_deletion_notify_players_using_the_sounds(self, publish):
        with self.captureOnCommitCallbacks(execute=True):
            self.artist.name = "Renamed artist"
            self.artist.save(update_fields=["name"])
        self.assert_notified(
            publish, [self.collecting_player.pk, self.playing_player.pk]
        )
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            self.artist.delete()
        self.assert_notified(
            publish, [self.collecting_player.pk, self.playing_player.pk]
        )
