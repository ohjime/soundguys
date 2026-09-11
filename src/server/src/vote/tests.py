import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.models import (
    Artist,
    Cosound,
    Listener,
    Manager,
    Player,
    Prediction,
    Sound,
    User,
)
from vote.models import Vote


class SubmitVoteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        manager_user = User.objects.create_user(
            username="manager",
            email="manager@example.com",
        )
        listener_user = User.objects.create_user(
            username="listener",
            email="listener@example.com",
        )
        cls.listener_user = listener_user
        cls.listener = Listener.objects.create(user=listener_user)
        cls.manager = Manager.objects.create(user=manager_user, name="Manager")

        cls.collected_sound = cls.create_sound("Already collected")
        cls.playing_sound = cls.create_sound("Currently playing")
        playing = Prediction.new()
        playing.add_layer(cls.playing_sound.pk, gain=0.75)
        cls.player = Player.objects.create(
            manager=cls.manager,
            name="Player",
            playing=playing,
        )

    @staticmethod
    def create_sound(title):
        return Sound.objects.create(
            file=f"sounds/{title.lower().replace(' ', '-')}.wav",
            title=title,
            embeddings=[0.0] * 5,
        )

    def setUp(self):
        self.client.force_login(self.listener_user)
        self.listener.collection.add(self.collected_sound)

    def submit_vote(self, choice):
        return self.client.post(
            reverse("vote:submit_vote"),
            {"player": self.player.token, "choice": choice, "section": "test"},
            headers={"HX-Request": "true"},
            query_params={
                "player": self.player.token,
                "choice": choice,
                "section": "test",
            },
        )

    def assert_vote_preserves_collection(self, choice, expected_value):
        collection_before = set(self.listener.collection.values_list("pk", flat=True))

        response = self.submit_vote(choice)

        self.assertEqual(response.status_code, 200)
        vote = Vote.objects.get()
        self.assertEqual(vote.voter, self.listener)
        self.assertEqual(vote.player, self.player)
        self.assertEqual(vote.value, expected_value)
        self.assertEqual(vote.section, "test")
        self.assertSetEqual(
            set(self.listener.collection.values_list("pk", flat=True)),
            collection_before,
        )
        trigger = json.loads(response.headers["HX-Trigger"])
        self.assertIn("vote-success", trigger)
        self.assertEqual(trigger["vote-success"]["voters"][0]["id"], vote.pk)

    def test_upvote_records_vote_without_changing_collection(self):
        self.assert_vote_preserves_collection(choice="1", expected_value=1)

    def test_downvote_records_vote_without_changing_collection(self):
        self.assert_vote_preserves_collection(choice="0", expected_value=0)


class SleepingActivationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        manager_user = User.objects.create_user(
            username="activation-manager",
            email="activation-manager@example.com",
        )
        cls.manager = Manager.objects.create(user=manager_user, name="Manager")
        cls.player = Player.objects.create(manager=cls.manager, name="Sleeping room")
        cls.user = User.objects.create_user(
            username="activator",
            email="activator@example.com",
        )
        cls.first_sound = Sound.objects.create(
            file="sounds/first.wav",
            title="First sound",
            embeddings=[0.0] * 5,
        )
        cls.second_sound = Sound.objects.create(
            file="sounds/second.wav",
            title="Second sound",
            embeddings=[0.0] * 5,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def submit(self, *, activation=True):
        data = {"activation": "1"} if activation else {}
        return self.client.post(
            reverse("vote:submit_vote"),
            data,
            headers={"HX-Request": "true"},
            query_params={
                "player": self.player.token,
                "choice": "1",
                "section": "door",
            },
        )

    def test_sleeping_request_wakes_with_random_collection_sound_without_vote_data(self):
        self.player.post.collection.add(self.first_sound, self.second_sound)

        with (
            patch("core.predict.random.choice", return_value=self.second_sound.pk),
            patch.object(Player, "announce") as announce,
        ):
            response = self.submit(activation=False)

        self.assertEqual(response.status_code, 200)
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(
            [layer["sound_id"] for layer in trigger["player-activated"]["layers"]],
            [self.second_sound.pk],
        )
        self.player.refresh_from_db()
        self.assertFalse(self.player.sleeping)
        self.assertIsNotNone(self.player.activated_at)
        self.assertEqual(
            [layer.sound_id for layer in self.player.playing.layers],
            [self.second_sound.pk],
        )
        self.assertEqual(self.player.playing.layers[0].sound_gain, 1.0)
        self.assertFalse(Listener.objects.filter(user=self.user).exists())
        self.assertFalse(Vote.objects.exists())
        self.assertFalse(Cosound.objects.exists())
        announce.assert_called_once()

    def test_activation_bypasses_an_existing_vote_cooldown(self):
        self.player.post.collection.add(self.first_sound)
        listener = Listener.objects.create(user=self.user)
        playing = Prediction.new()
        playing.add_layer(self.second_sound.pk)
        other_player = Player.objects.create(
            manager=self.manager,
            name="Other room",
            playing=playing,
        )
        cosound = Cosound.get_or_create_from_layers([(self.second_sound.pk, 1.0)])
        existing_vote = Vote.objects.create(
            voter=listener,
            player=other_player,
            cosound=cosound,
            value=Vote.UPVOTE,
        )

        page = self.client.get(
            reverse("vote:vote"),
            {
                "player": self.player.token,
                "choice": "1",
                "section": "door",
            },
        )
        self.assertEqual(page.context["throttle_seconds_left"], 0)

        with patch.object(Player, "announce"):
            response = self.submit()

        trigger = json.loads(response["HX-Trigger"])
        self.assertIn("player-activated", trigger)
        self.assertEqual(list(Vote.objects.all()), [existing_vote])
        self.assertEqual(Listener.objects.count(), 1)

    def test_stale_second_activation_is_idempotent_and_never_becomes_a_vote(self):
        self.player.post.collection.add(self.first_sound)
        with patch.object(Player, "announce"):
            first = self.submit()
        first_layers = json.loads(first["HX-Trigger"])["player-activated"]["layers"]

        with patch("vote.views.activate_player") as activate:
            second = self.submit()

        second_layers = json.loads(second["HX-Trigger"])["player-activated"][
            "layers"
        ]
        self.assertEqual(second_layers, first_layers)
        activate.assert_not_called()
        self.assertFalse(Vote.objects.exists())
        self.assertFalse(Listener.objects.exists())
        self.assertFalse(Cosound.objects.exists())

    def test_empty_collection_stays_sleeping_and_reports_unavailable(self):
        response = self.submit()

        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(
            trigger["player-activation-unavailable"]["message"],
            "This room has no sounds available yet.",
        )
        self.player.refresh_from_db()
        self.assertTrue(self.player.sleeping)
        self.assertIsNone(self.player.activated_at)
        self.assertEqual(self.player.playing.layers, [])
        self.assertFalse(Listener.objects.exists())
        self.assertFalse(Vote.objects.exists())
        self.assertFalse(Cosound.objects.exists())

    def test_deleted_prediction_sound_is_replaced_during_activation(self):
        stale_sound = Sound.objects.create(
            file="sounds/deleted-prediction.wav",
            title="Deleted prediction sound",
            embeddings=[0.0] * 5,
        )
        stale_prediction = Prediction.new()
        stale_prediction.add_layer(stale_sound.pk)
        Player.objects.filter(pk=self.player.pk).update(
            playing=stale_prediction,
            sleeping=False,
        )
        stale_sound.delete()
        self.player.post.collection.add(self.first_sound)

        with (
            patch("core.predict.random.choice", return_value=self.first_sound.pk),
            patch.object(Player, "announce") as announce,
        ):
            response = self.submit()

        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(
            [layer["sound_id"] for layer in trigger["player-activated"]["layers"]],
            [self.first_sound.pk],
        )
        self.player.refresh_from_db()
        self.assertFalse(self.player.sleeping)
        self.assertEqual(
            [layer.sound_id for layer in self.player.playing.layers],
            [self.first_sound.pk],
        )
        self.assertFalse(Vote.objects.exists())
        self.assertFalse(Listener.objects.exists())
        self.assertFalse(Cosound.objects.exists())
        announce.assert_called_once()

    def test_mixed_live_and_deleted_prediction_is_repaired_instead_of_voted_on(self):
        stale_sound = Sound.objects.create(
            file="sounds/deleted-mixed-prediction.wav",
            title="Deleted mixed prediction sound",
            embeddings=[0.0] * 5,
        )
        stale_prediction = Prediction.new()
        stale_prediction.add_layer(self.first_sound.pk, gain=0.5)
        stale_prediction.add_layer(stale_sound.pk, gain=0.5)
        Player.objects.filter(pk=self.player.pk).update(
            playing=stale_prediction,
            sleeping=False,
        )
        stale_sound.delete()
        self.player.post.collection.add(self.first_sound, self.second_sound)

        with (
            patch("core.predict.random.choice", return_value=self.second_sound.pk),
            patch.object(Player, "announce") as announce,
        ):
            response = self.submit(activation=False)

        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(
            [layer["sound_id"] for layer in trigger["player-activated"]["layers"]],
            [self.second_sound.pk],
        )
        self.player.refresh_from_db()
        self.assertEqual(
            [layer.sound_id for layer in self.player.playing.layers],
            [self.second_sound.pk],
        )
        self.assertFalse(Vote.objects.exists())
        self.assertFalse(Listener.objects.exists())
        self.assertFalse(Cosound.objects.exists())
        announce.assert_called_once()


class VoteCarouselCreditTests(TestCase):
    """The artist named on a voting layer, and where their name leads.

    Same rule as the library and explore cards: an artist with a page of their
    own is reached at it, and an artist without one is a plain name. The
    carousel decides by reading `artist_url` off the layer, so what matters
    here is that the payload carries one wherever there is a page to reach.
    """

    @classmethod
    def setUpTestData(cls):
        manager_user = User.objects.create_user(
            username="vote-manager", email="vote-manager@example.com"
        )
        cls.manager = Manager.objects.create(user=manager_user, name="Manager")
        cls.artist = Artist.objects.create(
            name="Cameron", url="https://cameron.example/"
        )
        cls.credited = Sound.objects.create(
            file="sounds/land.wav",
            title="Listening with the Land",
            artist=cls.artist,
            embeddings=[0.0] * 5,
        )
        cls.uncredited = Sound.objects.create(
            file="sounds/bell.wav",
            title="Harbour Bell",
            artist_legacy="Field Recordist",
            embeddings=[0.0] * 5,
        )
        playing = Prediction.new()
        playing.add_layer(cls.credited.pk, gain=0.75)
        playing.add_layer(cls.uncredited.pk, gain=0.5)
        cls.player = Player.objects.create(
            manager=cls.manager, name="UComm", playing=playing
        )

    def test_a_voting_layer_carries_the_artists_own_page(self):
        from vote.utils import serialize_player_for_carousel

        items = serialize_player_for_carousel(self.player)
        by_title = {item["sound_title"]: item for item in items}

        self.assertEqual(
            by_title["Listening with the Land"]["artist_url"],
            "https://cameron.example/",
        )
        # A legacy credit is a name in a text column, with no page behind it.
        self.assertEqual(by_title["Harbour Bell"]["artist_url"], "")

    def test_only_current_prediction_layers_are_in_the_display(self):
        from vote.utils import serialize_player_for_carousel

        items = serialize_player_for_carousel(self.player)

        self.assertEqual([item["sound_id"] for item in items], [self.credited.pk, self.uncredited.pk])
        self.assertTrue(all(item["kind"] == "layer" for item in items))
        self.assertTrue(all("sound_file" not in item for item in items))
