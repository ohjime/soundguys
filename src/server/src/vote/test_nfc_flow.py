import json
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from core.models import Listener, Manager, Player, Prediction, Sound, User
from core.predict import _predict_for_player
from vote.models import Vote


class VoteButtonParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.buttons = []
        self.core_cards = 0
        self.vote_cards = 0
        self.vote_layers = 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-core-card" in attrs:
            self.core_cards += 1
        if "data-vote-card" in attrs:
            self.vote_cards += 1
        if "data-vote-action-layer" in attrs:
            self.vote_layers += 1
        if tag == "button" and "data-nfc-vote" in attrs:
            self.buttons.append(attrs)


class NFCVoteFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = User.objects.create_user(
            username="nfc-manager", email="nfc-manager@example.com"
        )
        manager = Manager.objects.create(user=owner, name="Venue manager")
        cls.player = Player.objects.create(
            manager=manager, name="Listening room", playing=Prediction.new()
        )
        cls.user = User.objects.create_user(
            username="nfc-listener", email="nfc-listener@example.com"
        )
        cls.listener = Listener.objects.create(user=cls.user)
        cls.favorite = Sound.objects.create(
            title="Listener's rain", file="sounds/favorite.wav", embeddings=[0.0] * 5
        )
        cls.venue_sound = Sound.objects.create(
            title="Venue rain", file="sounds/venue.wav", embeddings=[0.0] * 5
        )
        cls.favorite.tags.add("rain")
        cls.venue_sound.tags.add("rain")
        cls.listener.collection.add(cls.favorite)
        cls.player.post.collection.add(cls.venue_sound)

    def params(self, choice="1"):
        return {
            "player": self.player.token,
            "choice": choice,
            "section": "East wall & garden / entrée",
        }

    def vote_url(self, choice="1"):
        return reverse("vote:submit_vote") + "?" + urlencode(self.params(choice))

    def page_button(self, response):
        self.assertEqual(response.status_code, 200)
        buttons = VoteButtonParser(response.content.decode()).buttons
        self.assertGreaterEqual(len(buttons), 1)
        return buttons[0]

    def page_buttons(self, response):
        self.assertEqual(response.status_code, 200)
        return VoteButtonParser(response.content.decode()).buttons

    def test_nfc_choices_render_enabled_buttons_even_before_playback_starts(self):
        for choice in ("0", "1"):
            with self.subTest(choice=choice):
                response = self.client.get(reverse("vote:vote"), self.params(choice))
                buttons = self.page_buttons(response)
                self.assertEqual(len(buttons), 2)
                for button in buttons:
                    self.assertNotIn("disabled", button)
                    self.assertEqual(button["type"], "button")
                    self.assertEqual(button["hx-swap"], "none")
                    self.assertEqual(urlsplit(button["hx-post"]).path, reverse("vote:submit_vote"))
                    self.assertEqual(
                        parse_qs(urlsplit(button["hx-post"]).query),
                        {key: [value] for key, value in self.params(choice).items()},
                    )
                self.assertEqual(buttons[0]["hx-vals"], '{"activation":"1"}')
                self.assertEqual(
                    buttons[1]["hx-vals"], '{"activation":"1","anonymous":"1"}'
                )
        self.assertFalse(Vote.objects.exists())

    def test_sleeping_action_is_an_activation_card_without_layer_chrome(self):
        response = self.client.get(reverse("vote:vote"), self.params())
        html = response.content.decode()
        page = VoteButtonParser(html)

        self.assertEqual(page.core_cards, 1)
        self.assertEqual(page.vote_cards, 1)
        self.assertEqual(page.vote_layers, 0)
        self.assertIn("data-player-activation-card", html)
        self.assertIn("data-player-activation-layer", html)
        self.assertNotIn("data-vote-action-card", html)
        self.assertNotIn("voteCardVisible", html)
        self.assertNotIn("$dispatch('card:remove')", html)
        self.assertIn("This room is resting.", html)
        self.assertNotIn("Tap the green card to vote", html)
        self.assertIn('id="vote-card-header"', html)
        self.assertNotIn('aria-label="Close card"', html)
        self.assertEqual(len(page.buttons), 2)

    def test_tab_navigation_keeps_the_nfc_action_without_casting_a_vote(self):
        self.client.force_login(self.user)
        for name in ("vote", "vote_initial", "vote_tab"):
            with self.subTest(route=name):
                response = self.client.get(
                    reverse(f"vote:{name}"), self.params(), HTTP_HX_REQUEST="true"
                )
                button = self.page_button(response)
                self.assertEqual(button["hx-post"], self.vote_url())
                self.assertEqual(button["hx-vals"], '{"activation":"1"}')
        self.client.get(reverse("vote:about"), self.params(), HTTP_HX_REQUEST="true")
        self.assertFalse(Vote.objects.exists())

    def test_missing_invalid_or_unknown_nfc_target_has_no_vote_button(self):
        cases = [
            {"player": self.player.token},
            self.params(""),
            self.params("-1"),
            self.params("yes"),
            {"player": "unknown", "choice": "1"},
            {"choice": "1"},
        ]
        for params in cases:
            with self.subTest(params=params):
                response = self.client.get(reverse("vote:vote"), params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(VoteButtonParser(response.content.decode()).buttons, [])
                self.assertNotIn("data-vote-action-layer", response.content.decode())
        self.assertFalse(Vote.objects.exists())

    def test_each_choice_activates_empty_playback_without_recording_a_vote(self):
        self.client.force_login(self.user)
        for choice in ("0", "1"):
            with self.subTest(choice=choice):
                self.player.update(Prediction.new())
                with patch.object(Player, "announce"):
                    response = self.client.post(
                        self.vote_url(choice),
                        {"activation": "1"},
                        HTTP_HX_REQUEST="true",
                    )
                self.assertEqual(response.status_code, 200)
                self.assertIn("player-activated", json.loads(response["HX-Trigger"]))
                self.assertFalse(Vote.objects.exists())
                self.player.refresh_from_db()
                self.assertFalse(self.player.sleeping)
                self.assertEqual(
                    [layer.sound_id for layer in self.player.playing.layers],
                    [self.venue_sound.pk],
                )
                self.assertEqual(list(self.listener.collection.all()), [self.favorite])
                self.assertEqual(list(self.player.post.collection.all()), [self.venue_sound])

    def test_anonymous_activation_authenticates_and_starts_playback(self):
        with patch.object(Player, "announce"):
            response = self.client.post(
                self.vote_url(),
                {"activation": "1", "anonymous": "1"},
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("player-activated", json.loads(response["HX-Trigger"]))
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertTrue(response.wsgi_request.user.email.endswith("@anon.cosound.ca"))
        self.assertFalse(Vote.objects.exists())
        self.player.refresh_from_db()
        self.assertFalse(self.player.sleeping)

    def test_active_guest_can_vote_anonymously_without_entering_activation(self):
        playing = Prediction.new()
        playing.add_layer(self.venue_sound.pk, gain=0.5)
        self.player.update(playing)

        response = self.client.post(
            self.vote_url("0"),
            {"anonymous": "1"},
            HTTP_HX_REQUEST="true",
        )

        trigger = json.loads(response["HX-Trigger"])
        self.assertIn("vote-success", trigger)
        self.assertNotIn("player-activated", trigger)
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertTrue(response.wsgi_request.user.email.endswith("@anon.cosound.ca"))
        vote = Vote.objects.get()
        self.assertEqual(vote.value, 0)
        self.assertEqual(vote.section, self.params("0")["section"])
        self.player.refresh_from_db()
        self.assertFalse(self.player.sleeping)
        self.assertEqual(
            [layer.sound_id for layer in self.player.playing.layers],
            [self.venue_sound.pk],
        )

    def test_active_page_that_sleeps_before_submit_wakes_without_recording_vote(self):
        self.client.force_login(self.user)
        playing = Prediction.new()
        playing.add_layer(self.venue_sound.pk)
        self.player.update(playing)
        page = self.client.get(reverse("vote:vote"), self.params())
        button = self.page_button(page)
        self.assertNotIn("hx-vals", button)

        self.player.update(Prediction.new())
        with patch.object(Player, "announce"):
            response = self.client.post(
                button["hx-post"],
                HTTP_HX_REQUEST="true",
            )

        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(
            [layer["sound_id"] for layer in trigger["player-activated"]["layers"]],
            [self.venue_sound.pk],
        )
        self.assertFalse(Vote.objects.exists())
        self.player.refresh_from_db()
        self.assertFalse(self.player.sleeping)

    def test_existing_cooldown_prevents_a_second_vote(self):
        self.client.force_login(self.user)
        playing = Prediction.new()
        playing.add_layer(self.venue_sound.pk)
        self.player.update(playing)
        first = self.client.post(self.vote_url(), HTTP_HX_REQUEST="true")
        self.assertIn("vote-success", json.loads(first["HX-Trigger"]))
        response = self.client.post(self.vote_url("0"), HTTP_HX_REQUEST="true")
        self.assertEqual(Vote.objects.count(), 1)
        self.assertGreater(json.loads(response["HX-Trigger"])["vote-throttled"]["seconds_left"], 0)
        page = self.client.get(reverse("vote:vote"), self.params())
        self.assertGreater(page.context["throttle_seconds_left"], 0)
        self.assertIn("disabled", self.page_button(page))

    def test_guest_login_allows_the_original_button_post_to_resume_with_rotated_csrf(self):
        client = Client(enforce_csrf_checks=True)
        page = client.get(reverse("vote:vote"), self.params())
        button_url = self.page_button(page)["hx-post"]
        original_csrf = client.cookies["csrftoken"].value
        gated = client.post(
            button_url,
            {"activation": "1"},
            HTTP_HX_REQUEST="true",
            HTTP_X_CSRFTOKEN=original_csrf,
        )
        self.assertEqual(gated["HX-Trigger"], "auth-required")
        self.assertEqual(gated["HX-Retarget"], "#core_modal_content")
        self.assertContains(gated, "Continue Anonymously")
        self.assertFalse(Vote.objects.exists())
        login = client.post(
            reverse("login:login_anonymously"),
            HTTP_HX_REQUEST="true",
            HTTP_X_CSRFTOKEN=original_csrf,
        )
        self.assertEqual(login.status_code, 200)
        self.assertTrue(json.loads(login["HX-Trigger"])["auth-success"])
        self.assertContains(login, 'id="core-header"')
        self.assertNotEqual(client.cookies["csrftoken"].value, original_csrf)
        resumed = client.post(
            button_url,
            {"activation": "1"},
            HTTP_HX_REQUEST="true",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(resumed.status_code, 200)
        self.assertIn("player-activated", json.loads(resumed["HX-Trigger"]))
        self.assertFalse(Vote.objects.exists())
        self.player.refresh_from_db()
        self.assertFalse(self.player.sleeping)

    def test_listener_vote_starts_prediction_from_the_local_posts_collection(self):
        self.client.force_login(self.user)
        playing = Prediction.new()
        playing.add_layer(self.venue_sound.pk, gain=0.5)
        self.player.update(playing)
        page = self.client.get(reverse("vote:vote"), self.params())
        button_url = self.page_button(page)["hx-post"]
        response = self.client.post(button_url, HTTP_HX_REQUEST="true")
        self.assertIn("vote-success", json.loads(response["HX-Trigger"]))
        with patch.object(Player, "announce") as announce:
            self.assertEqual(_predict_for_player(self.player.pk), 1)
        self.player.refresh_from_db()
        self.assertEqual(
            [layer.sound_id for layer in self.player.playing.layers],
            [self.venue_sound.pk],
        )
        self.assertEqual(list(self.listener.collection.all()), [self.favorite])
        announce.assert_called_once()
