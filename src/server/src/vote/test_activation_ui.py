from html.parser import HTMLParser

from django.test import TestCase
from django.urls import reverse

from core.models import Manager, Player, Prediction, Sound, User


class NFCActionParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.actions = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "button" and "data-nfc-vote" in attrs:
            self.actions.append(attrs)


class SleepingPlayerCardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = User.objects.create_user(
            username="activation-manager", email="activation@example.com"
        )
        manager = Manager.objects.create(user=owner, name="Activation manager")
        cls.player = Player.objects.create(
            manager=manager,
            name="Resting room",
            playing=Prediction.new(),
        )
        cls.sound = Sound.objects.create(
            title="Morning birds",
            file="sounds/morning-birds.wav",
            embeddings=[0.0] * 5,
        )

    def page(self):
        return self.client.get(
            reverse("vote:vote"),
            {"player": self.player.token, "choice": "1", "section": "entry"},
        )

    def test_sleeping_player_renders_auth_and_anonymous_activation_actions(self):
        response = self.page()
        html = response.content.decode()
        actions = NFCActionParser(html).actions

        self.assertTrue(response.context["sleeping"])
        self.assertContains(response, 'data-player-sleeping="true"')
        self.assertContains(response, "data-player-activation-card")
        self.assertContains(response, "data-player-activation-layer")
        self.assertContains(response, "ACTIVATE")
        self.assertEqual(len(actions), 2)
        self.assertIn("data-activation-auth", actions[0])
        self.assertIn("data-activation-anonymous", actions[1])
        self.assertContains(response, "OR")
        self.assertContains(response, 'id="vote-card-header"')
        self.assertEqual(actions[0]["aria-label"], "Sign in and activate this space")
        self.assertEqual(actions[1]["aria-label"], "Activate this space anonymously")
        self.assertEqual(actions[0]["hx-vals"], '{"activation":"1"}')
        self.assertEqual(
            actions[1]["hx-vals"], '{"activation":"1","anonymous":"1"}'
        )

    def test_authenticated_listener_sees_one_activation_action(self):
        self.client.force_login(self.player.manager.user)
        response = self.page()
        actions = NFCActionParser(response.content.decode()).actions

        self.assertEqual(len(actions), 1)
        self.assertIn("data-activation-authenticated", actions[0])
        self.assertEqual(actions[0]["aria-label"], "Activate this space")
        self.assertEqual(actions[0]["hx-vals"], '{"activation":"1"}')

    def test_sleeping_player_omits_empty_layer_and_playback_chrome(self):
        response = self.page()

        self.assertNotContains(response, "Nothing is playing")
        self.assertNotContains(response, "NO LAYERS")
        self.assertContains(response, 'id="vote-card-header"')
        self.assertNotContains(response, "data-vote-action-layer")
        self.assertNotContains(response, 'aria-label="Show what is playing"')
        self.assertNotContains(response, 'aria-label="Save current sounds"')

    def test_awaken_is_not_rendered_when_sleeping_flag_is_false(self):
        Player.objects.filter(pk=self.player.pk).update(sleeping=False)
        self.player.refresh_from_db()
        response = self.page()

        self.assertFalse(response.context["sleeping"])
        self.assertContains(response, 'data-player-sleeping="false"')
        self.assertNotContains(response, "data-player-activation-card")
        self.assertNotContains(response, "AWAKEN")
        self.assertContains(response, "data-vote-action-layer")

    def test_active_player_keeps_the_normal_vote_and_layer_controls(self):
        playing = Prediction.new()
        playing.add_layer(self.sound.pk, gain=0.6)
        self.player.update(playing)
        self.player.refresh_from_db()
        response = self.page()
        html = response.content.decode()
        actions = NFCActionParser(html).actions

        self.assertFalse(response.context["sleeping"])
        self.assertContains(response, 'data-player-sleeping="false"')
        self.assertNotContains(response, "data-player-activation-card")
        self.assertNotContains(response, "AWAKEN")
        self.assertContains(response, "data-vote-action-layer")
        self.assertContains(response, "Morning birds")
        self.assertContains(response, 'id="vote-card-header"')
        self.assertContains(response, 'aria-label="Save current sounds"')
        self.assertEqual(len(actions), 2)
        self.assertNotIn("hx-vals", actions[0])
        self.assertIn("data-vote-auth", actions[0])
        self.assertIn("data-vote-anonymous", actions[1])
        self.assertEqual(actions[1]["hx-vals"], '{"anonymous":"1"}')
