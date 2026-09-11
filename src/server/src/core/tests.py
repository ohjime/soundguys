from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from taggit.models import Tag

from core.management.commands.refresh import Command, REFRESH_INTERVAL_SECONDS
from core.models import (
    Post,
    Artist,
    Cosound,
    Listener,
    LocalPost,
    Manager,
    Player,
    Prediction,
    Sound,
    User,
)
from core.predict import ACTIVITY_WINDOW, _predict_for_player, activate_player
from vote.models import Vote


class RefreshSchedulerTests(SimpleTestCase):
    @patch(
        "core.management.commands.refresh.time.sleep",
        side_effect=KeyboardInterrupt,
    )
    @patch("core.management.commands.refresh.Player.objects.filter", return_value=[])
    @patch("core.management.commands.refresh._get_predictor")
    def test_waits_thirty_seconds_between_player_refreshes(
        self,
        _get_predictor,
        _players,
        sleep,
    ):
        with self.assertRaises(SystemExit) as stopped:
            Command().handle()

        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(REFRESH_INTERVAL_SECONDS, 30)
        _players.assert_called_once_with(sleeping=False)
        sleep.assert_called_once_with(30)

    @patch(
        "core.management.commands.refresh.time.sleep",
        side_effect=KeyboardInterrupt,
    )
    @patch("core.management.commands.refresh.Player.objects.filter")
    @patch("core.management.commands.refresh._get_predictor")
    def test_only_awake_players_are_enqueued(
        self,
        get_predictor,
        players,
        _sleep,
    ):
        awake = SimpleNamespace(pk=7, name="Awake room")
        players.return_value = [awake]

        with self.assertRaises(SystemExit):
            Command().handle()

        players.assert_called_once_with(sleeping=False)
        get_predictor.return_value.enqueue.assert_called_once_with(player_id=awake.pk)


class ListenerTestPointAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="admin-password",
        )
        cls.listener_user = User.objects.create_user(
            username="listener",
            email="listener@example.com",
        )
        cls.listener = Listener.objects.create(user=cls.listener_user)

        cls.ambient_sound = cls.create_sound("Ambient one")
        cls.second_ambient_sound = cls.create_sound("Ambient two")
        cls.old_sound = cls.create_sound("Old favourite")
        cls.ambient_sound.tags.add("ambient")
        cls.second_ambient_sound.tags.add("ambient", "field")
        cls.old_sound.tags.add("legacy")

        cls.ambient_tag = Tag.objects.get(name="ambient")
        cls.empty_tag = Tag.objects.create(name="unused", slug="unused")

        cls.manager = Manager.objects.create(user=cls.admin, name="Test manager")
        cls.player = Player.objects.create(manager=cls.manager, name="Test player")
        cls.cosound = Cosound.objects.create(hashset="hashset", hashid="hashid")
        cls.vote = Vote.objects.create(
            voter=cls.listener,
            player=cls.player,
            cosound=cls.cosound,
            value=Vote.UPVOTE,
            section="before-action",
        )

    @staticmethod
    def create_sound(title):
        return Sound.objects.create(
            file=f"sounds/{title.lower().replace(' ', '-')}.wav",
            title=title,
            embeddings=[0, 0, 0, 0, 0],
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.change_url = reverse(
            "admin:core_listener_change", args=[self.listener.pk]
        )
        self.action_url = reverse(
            "admin:core_listener_set_test_point", args=[self.listener.pk]
        )

    def test_change_page_shows_test_point_control_with_current_tags(self):
        response = self.client.get(self.change_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Set test point")
        self.assertContains(response, f'value="{self.ambient_tag.pk}"')
        self.assertContains(response, ">ambient</option>")
        self.assertContains(response, f'value="{self.empty_tag.pk}"')
        self.assertContains(response, ">unused</option>")
        self.assertContains(response, f'action="{self.action_url}"')

    def test_action_replaces_collection_and_preserves_votes(self):
        self.listener.collection.add(self.old_sound, self.ambient_sound)

        response = self.client.post(
            self.action_url,
            {"test_point_tag": self.ambient_tag.pk},
        )

        self.assertRedirects(response, self.change_url, fetch_redirect_response=False)
        self.assertSetEqual(
            set(self.listener.collection.all()),
            {self.ambient_sound, self.second_ambient_sound},
        )
        self.vote.refresh_from_db()
        self.assertEqual(self.vote.value, Vote.UPVOTE)
        self.assertEqual(self.vote.section, "before-action")

        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn(
            'Set test point to "ambient" and replaced the collection with 2 sounds.',
            messages,
        )

    def test_tag_without_sounds_clears_collection_and_reports_result(self):
        self.listener.collection.add(self.old_sound, self.ambient_sound)

        response = self.client.post(
            self.action_url,
            {"test_point_tag": self.empty_tag.pk},
        )

        self.assertRedirects(response, self.change_url, fetch_redirect_response=False)
        self.assertFalse(self.listener.collection.exists())
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn(
            'Set test point to "unused". No sounds use this tag, so the collection is now empty.',
            messages,
        )

    def test_invalid_tag_does_not_change_collection(self):
        self.listener.collection.add(self.old_sound)

        response = self.client.post(
            self.action_url,
            {"test_point_tag": "not-a-tag-id"},
        )

        self.assertRedirects(response, self.change_url, fetch_redirect_response=False)
        self.assertSetEqual(set(self.listener.collection.all()), {self.old_sound})
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn(
            "Select a valid tag before setting a test point.",
            messages,
        )

    def test_action_rejects_get_requests(self):
        response = self.client.get(self.action_url)

        self.assertEqual(response.status_code, 405)

    def test_action_requires_listener_change_permission(self):
        viewer = User.objects.create_user(
            username="viewer",
            email="viewer@example.com",
            is_staff=True,
        )
        viewer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="core",
                codename="view_listener",
            )
        )
        self.client.force_login(viewer)
        self.listener.collection.add(self.old_sound)

        response = self.client.post(
            self.action_url,
            {"test_point_tag": self.ambient_tag.pk},
        )

        self.assertEqual(response.status_code, 403)
        self.assertSetEqual(set(self.listener.collection.all()), {self.old_sound})


class PredictorTests(TestCase):
    def setUp(self):
        manager_user = User.objects.create_user(
            username="manager",
            email="manager@example.com",
            password="password",
        )
        self.manager = Manager.objects.create(user=manager_user, name="Manager")
        self.player = Player.objects.create(manager=self.manager, name="Player")
        Player.objects.filter(pk=self.player.pk).update(sleeping=False)
        self.player.sleeping = False
        self.cosound = Cosound.objects.create(hashid="vote", hashset="vote")
        self.listener_number = 0

    def make_sound(self, title, *tags):
        sound = Sound.objects.create(
            file=f"sounds/{title}.mp3",
            title=title,
            embeddings=[0.0] * 5,
        )
        sound.tags.add(*tags)
        return sound

    def make_listener(self, *sounds):
        self.listener_number += 1
        number = self.listener_number
        user = User.objects.create_user(
            username=f"listener-{number}",
            email=f"listener-{number}@example.com",
            password="password",
        )
        listener = Listener.objects.create(user=user)
        listener.collection.add(*sounds)
        return listener

    def vote(self, listener, *, player=None, created_at=None, value=Vote.UPVOTE):
        vote = Vote.objects.create(
            voter=listener,
            player=player or self.player,
            cosound=self.cosound,
            value=value,
        )
        if created_at is not None:
            Vote.objects.filter(pk=vote.pk).update(created_at=created_at)
        return vote

    def predict(self):
        with patch.object(Player, "announce"):
            return _predict_for_player(self.player.pk)

    def test_only_voters_from_the_last_five_minutes_are_active(self):
        rock = self.make_sound("rock", "rock")
        jazz = self.make_sound("jazz", "jazz")
        self.player.post.collection.add(rock, jazz)
        recent_listener = self.make_listener(rock)
        stale_listener = self.make_listener(jazz)
        other_player_listener = self.make_listener(jazz)
        other_player = Player.objects.create(
            manager=self.manager,
            name="Other Player",
        )
        now = timezone.now()
        self.vote(recent_listener, created_at=now - timedelta(minutes=4, seconds=59))
        self.vote(stale_listener, created_at=now - timedelta(minutes=5, seconds=1))
        self.vote(other_player_listener, player=other_player)

        self.assertEqual(self.predict(), 1)

        self.player.refresh_from_db()
        self.assertEqual(
            [layer.sound_id for layer in self.player.playing.layers],
            [rock.pk],
        )

    def test_multiple_votes_from_one_listener_produce_one_layer(self):
        sound = self.make_sound("ambient", "ambient")
        self.player.post.collection.add(sound)
        listener = self.make_listener(sound)
        self.vote(listener)
        self.vote(listener, value=Vote.DOWNVOTE)
        self.vote(listener)

        self.assertEqual(self.predict(), 1)

        self.player.refresh_from_db()
        self.assertEqual(len(self.player.playing.layers), 1)
        self.assertEqual(self.player.playing.layers[0].sound_gain, 1.0)

    def test_each_listener_contributes_a_layer_from_their_own_top_tag(self):
        library_rock = self.make_sound("library-rock", "rock")
        library_jazz = self.make_sound("library-jazz", "jazz")
        self.player.post.collection.add(library_rock, library_jazz)
        rock_one = self.make_sound("rock-one", "rock")
        rock_two = self.make_sound("rock-two", "rock")
        jazz_one = self.make_sound("jazz-one", "jazz")
        jazz_two = self.make_sound("jazz-two", "jazz")
        rock_listener = self.make_listener(rock_one, rock_two)
        jazz_listener = self.make_listener(jazz_one, jazz_two)
        self.vote(rock_listener)
        self.vote(jazz_listener)

        self.assertEqual(self.predict(), 1)

        self.player.refresh_from_db()
        self.assertEqual(
            [layer.sound_id for layer in self.player.playing.layers],
            [library_rock.pk, library_jazz.pk],
        )
        self.assertEqual(
            [layer.sound_gain for layer in self.player.playing.layers],
            [1.0, 1.0],
        )

    def test_tied_usable_top_tags_are_selected_randomly(self):
        rock = self.make_sound("library-rock", "rock")
        jazz = self.make_sound("library-jazz", "jazz")
        self.player.post.collection.add(rock, jazz)
        collected = self.make_sound("collected", "rock", "jazz")
        listener = self.make_listener(collected)
        self.vote(listener)
        jazz_tag_id = jazz.tags.get().pk

        def choose_jazz(options):
            if isinstance(options[0], int):
                return jazz_tag_id
            return options[0]

        with patch("core.predict.random.choice", side_effect=choose_jazz) as choice:
            self.assertEqual(self.predict(), 1)

        self.player.refresh_from_db()
        self.assertEqual(self.player.playing.layers[0].sound_id, jazz.pk)
        self.assertEqual(choice.call_count, 2)

    def test_uses_a_matching_tag_when_another_tied_top_tag_is_unavailable(self):
        jazz = self.make_sound("library-jazz", "jazz")
        self.player.post.collection.add(jazz)
        collected = self.make_sound("collected", "jazz", "unavailable")
        listener = self.make_listener(collected)
        self.vote(listener)

        self.assertEqual(self.predict(), 1)

        self.player.refresh_from_db()
        self.assertEqual(self.player.playing.layers[0].sound_id, jazz.pk)

    def test_does_not_fall_back_to_a_less_frequent_tag(self):
        jazz = self.make_sound("library-jazz", "jazz")
        self.player.post.collection.add(jazz)
        unavailable_one = self.make_sound("unavailable-one", "unavailable")
        unavailable_two = self.make_sound("unavailable-two", "unavailable")
        collected_jazz = self.make_sound("collected-jazz", "jazz")
        listener = self.make_listener(
            unavailable_one,
            unavailable_two,
            collected_jazz,
        )
        self.vote(listener)

        self.assertEqual(self.predict(), 0)

        self.player.refresh_from_db()
        self.assertEqual(self.player.playing.layers, [])

    def test_selected_sound_is_restricted_to_the_players_library(self):
        library_sound = self.make_sound("library", "ambient")
        outside_sound = self.make_sound("outside", "ambient")
        self.player.post.collection.add(library_sound)
        listener = self.make_listener(outside_sound)
        self.vote(listener)

        self.assertEqual(self.predict(), 1)

        self.player.refresh_from_db()
        self.assertEqual(self.player.playing.layers[0].sound_id, library_sound.pk)

    def test_switching_posts_predicts_from_the_new_posts_collection(self):
        previous_sound = self.make_sound("previous-library", "ambient")
        next_sound = self.make_sound("next-library", "ambient")
        self.player.post.collection.add(previous_sound)
        listener = self.make_listener(previous_sound)
        self.vote(listener)
        self.assertEqual(self.predict(), 1)
        self.player.refresh_from_db()
        self.assertEqual(self.player.playing.layers[0].sound_id, previous_sound.pk)

        previous_post = self.player.post
        next_post = LocalPost.objects.create(post=Post.objects.create(
            composer=self.manager.user, title="The next local post"
        ))
        next_post.collection.add(next_sound)
        self.player.post = next_post
        self.player.save(update_fields=["post"])
        self.assertEqual(self.predict(), 1)
        self.player.refresh_from_db()
        self.assertEqual(self.player.playing.layers[0].sound_id, next_sound.pk)
        self.assertEqual(self.player.library(), [next_sound])
        self.assertEqual(list(previous_post.collection.all()), [previous_sound])

    def test_same_top_tag_uses_distinct_matching_sounds(self):
        library_one = self.make_sound("library-one", "ambient")
        library_two = self.make_sound("library-two", "ambient")
        self.player.post.collection.add(library_one, library_two)
        listener_one = self.make_listener(
            self.make_sound("collected-one", "ambient")
        )
        listener_two = self.make_listener(
            self.make_sound("collected-two", "ambient")
        )
        self.vote(listener_one)
        self.vote(listener_two)

        self.assertEqual(self.predict(), 1)

        self.player.refresh_from_db()
        sound_ids = [layer.sound_id for layer in self.player.playing.layers]
        self.assertEqual(len(sound_ids), 2)
        self.assertEqual(set(sound_ids), {library_one.pk, library_two.pk})

    def test_same_top_tag_with_one_matching_sound_adds_it_only_once(self):
        library_sound = self.make_sound("library", "ambient")
        self.player.post.collection.add(library_sound)
        listener_one = self.make_listener(
            self.make_sound("collected-one", "ambient")
        )
        listener_two = self.make_listener(
            self.make_sound("collected-two", "ambient")
        )
        self.vote(listener_one)
        self.vote(listener_two)

        self.assertEqual(self.predict(), 1)

        self.player.refresh_from_db()
        self.assertEqual(
            [layer.sound_id for layer in self.player.playing.layers],
            [library_sound.pk],
        )

    def test_empty_and_unmatched_collections_leave_prediction_unchanged(self):
        existing_sound = self.make_sound("existing", "existing")
        self.player.playing = Prediction.new()
        self.player.playing.add_layer(existing_sound.pk, gain=0.25)
        self.player.save()
        empty_listener = self.make_listener()
        unmatched = self.make_sound("unmatched", "unmatched")
        unmatched_listener = self.make_listener(unmatched)
        self.vote(empty_listener)
        self.vote(unmatched_listener)

        self.assertEqual(self.predict(), 0)

        self.player.refresh_from_db()
        self.assertEqual(len(self.player.playing.layers), 1)
        self.assertEqual(self.player.playing.layers[0].sound_id, existing_sound.pk)
        self.assertEqual(self.player.playing.layers[0].sound_gain, 0.25)

    def test_no_recent_votes_clear_existing_prediction_and_api_layers(self):
        existing_sound = self.make_sound("existing", "existing")
        self.player.playing = Prediction.new()
        self.player.playing.add_layer(existing_sound.pk, gain=0.5)
        self.player.save()
        stale_listener = self.make_listener(existing_sound)
        self.vote(
            stale_listener,
            created_at=timezone.now() - timedelta(minutes=5, seconds=1),
        )

        with patch("core.predict.random.choice") as choice:
            self.assertEqual(self.predict(), 0)

        self.player.refresh_from_db()
        self.assertIsInstance(self.player.playing, Prediction)
        self.assertEqual(self.player.playing.layers, [])
        self.assertTrue(self.player.sleeping)
        self.assertIsNone(self.player.activated_at)
        choice.assert_not_called()

        response = self.client.get(
            "/api/player",
            headers={"X-API-Key": self.player.token},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["layers"], [])

    def test_sleeping_player_returns_before_reading_votes(self):
        Player.objects.filter(pk=self.player.pk).update(sleeping=True)

        with patch("core.predict.Vote.recent") as recent:
            self.assertEqual(self.predict(), 0)

        recent.assert_not_called()

    def test_activation_prediction_lives_for_the_activity_window_then_sleeps(self):
        sound = self.make_sound("wake-up", "ambient")
        self.player.post.collection.add(sound)

        with patch("core.predict.random.choice", return_value=sound.pk):
            prediction = activate_player(self.player)

        self.assertIsNotNone(prediction)
        self.player.refresh_from_db()
        activated_at = self.player.activated_at
        self.assertIsNotNone(activated_at)
        self.assertFalse(self.player.sleeping)
        self.assertEqual(
            [layer.sound_id for layer in self.player.playing.layers],
            [sound.pk],
        )

        with patch(
            "core.predict.timezone.now",
            return_value=activated_at + ACTIVITY_WINDOW - timedelta(seconds=1),
        ):
            self.assertEqual(self.predict(), 0)

        self.player.refresh_from_db()
        self.assertFalse(self.player.sleeping)
        self.assertEqual(
            [layer.sound_id for layer in self.player.playing.layers],
            [sound.pk],
        )

        with patch(
            "core.predict.timezone.now",
            return_value=activated_at + ACTIVITY_WINDOW + timedelta(seconds=1),
        ):
            self.assertEqual(self.predict(), 0)

        self.player.refresh_from_db()
        self.assertTrue(self.player.sleeping)
        self.assertIsNone(self.player.activated_at)
        self.assertEqual(self.player.playing.layers, [])


class SoundCreditTests(TestCase):
    """The artist named on a layer, and where pressing that name goes.

    An artist with a page of their own is reached at it directly, which is what
    keeps their profile theirs to run rather than ours to host. Everyone else
    falls back to the details modal, and the card decides between the two by
    reading this field — so what matters here is that every layer carries one,
    and that it is empty exactly when there is no page to send anybody to.
    """

    def make_sound(self, title="Rain on Tin", **fields):
        """A Sound with its embedding supplied, so save() skips the classifier."""
        return Sound.objects.create(
            file=f"sounds/{title.lower().replace(' ', '-')}.wav",
            title=title,
            embeddings=[0, 0, 0, 0, 0],
            **fields,
        )

    def test_a_layer_carries_the_artists_own_page_beside_their_name(self):
        artist = Artist.objects.create(
            name="Cameron", url="https://cameron.example/"
        )
        layer = self.make_sound(artist=artist).asLayer()

        self.assertEqual(layer["sound_artist"], "Cameron")
        self.assertEqual(layer["artist_url"], "https://cameron.example/")

    def test_an_artist_who_has_given_no_page_offers_none(self):
        """Which is what leaves the press to open our own details modal."""
        artist = Artist.objects.create(name="Sam")

        self.assertEqual(self.make_sound(artist=artist).artist_url, "")

    def test_a_legacy_credit_has_no_page_to_offer(self):
        """A name in a text column has no Artist row to keep a URL on."""
        sound = self.make_sound(artist_legacy="Field Recordist")

        self.assertEqual(sound.artist_name, "Field Recordist")
        self.assertEqual(sound.artist_url, "")

    def test_a_stored_mix_hands_the_credit_through_to_the_card(self):
        """as_layers is what the explore post and the library card mount."""
        artist = Artist.objects.create(
            name="Cameron", url="https://cameron.example/"
        )
        sound = self.make_sound(artist=artist)
        cosound = Cosound.get_or_create_from_layers([(sound.pk, 0.5)])

        [layer] = cosound.as_layers()

        self.assertEqual(layer["artist_url"], "https://cameron.example/")
