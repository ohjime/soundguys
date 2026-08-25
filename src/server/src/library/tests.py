import json

from django.test import TestCase
from django.urls import reverse

from core.models import Cosound, Listener, Sound, User
from library.models import SoundMix


class LibraryKeepSoundTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="listener",
            email="listener@example.com",
            password="password",
        )
        cls.sound = Sound.objects.create(
            file="sounds/heart-test.wav",
            title="Heart test",
            embeddings=[0, 0, 0, 0, 0],
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse("library:keep_sound")

    def post_toggle(self):
        return self.client.post(
            self.url,
            {"sound_id": self.sound.pk},
            HTTP_HX_REQUEST="true",
        )

    def test_adds_sound_and_reports_saved_state(self):
        response = self.post_toggle()

        listener = Listener.objects.get(user=self.user)
        self.assertTrue(listener.collection.filter(pk=self.sound.pk).exists())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.headers["HX-Trigger"]),
            {
                "layer-saved": {
                    "saved": True,
                    "soundId": str(self.sound.pk),
                }
            },
        )

    def test_removes_sound_and_reports_unsaved_state(self):
        listener = Listener.objects.create(user=self.user)
        listener.collection.add(self.sound)

        response = self.post_toggle()

        self.assertFalse(listener.collection.filter(pk=self.sound.pk).exists())
        self.assertEqual(
            json.loads(response.headers["HX-Trigger"])["layer-saved"],
            {"saved": False, "soundId": str(self.sound.pk)},
        )

    def test_unauthenticated_heart_opens_login_modal(self):
        self.client.logout()

        response = self.post_toggle()

        self.assertEqual(response["HX-Retarget"], "#core_modal_content")
        self.assertEqual(response["HX-Reswap"], "innerHTML")
        self.assertEqual(response["HX-Trigger-After-Swap"], "show-modal")
        self.assertEqual(response["HX-Trigger"], "auth-required")
        self.assertContains(response, "Login")
        self.assertNotContains(response, "data-core-card")


class LibrarySwapTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="library_user",
            email="library_user@example.com",
            password="password",
        )
        cls.sound = Sound.objects.create(
            file="sounds/swap-test.wav",
            title="Forest Rain",
            embeddings=[0, 0, 0, 0, 0],
        )

    def setUp(self):
        self.url = reverse("library:swap")

    def test_unauthenticated_swap_allowed(self):
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Forest Rain")
        self.assertContains(response, 'id="cancel-swap-button-list"')
        self.assertContains(response, 'id="cancel-swap-button-search"')

    def test_non_htmx_request_denied(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Request Denied.")

    def test_swap_view_renders_collection_and_cancel_buttons(self):
        self.client.force_login(self.user)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Forest Rain")
        self.assertContains(response, 'id="cancel-swap-button-list"')
        self.assertContains(response, 'id="cancel-swap-button-search"')
        self.assertContains(response, "Back to the mix")
        self.assertContains(response, "Cancel")


class LibrarySavedListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="saved-listener",
            email="saved-listener@example.com",
            password="password",
        )
        cls.sound = Sound.objects.create(
            file="sounds/saved-list.wav",
            title="Night birds",
            artist_legacy="Field Recordist",
            embeddings=[0, 0, 0, 0, 0],
        )
        listener = Listener.objects.create(user=cls.user)
        listener.collection.add(cls.sound)
        cosound = Cosound.get_or_create_from_layers([(cls.sound.pk, 1)])
        SoundMix.objects.create(
            creator=cls.user,
            cosound=cosound,
            title="Evening mix",
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.liked_url = reverse("library:liked_list")
        self.saved_url = reverse("library:saved_list")

    def test_liked_sounds_card_is_added_to_the_deck(self):
        response = self.client.get(self.liked_url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Retarget"], "#deck")
        self.assertEqual(response["HX-Reswap"], "beforeend")
        self.assertContains(response, "Liked Sounds")
        self.assertContains(response, "Night birds")
        self.assertContains(response, "Field Recordist")
        self.assertNotContains(response, "Evening mix")

    def test_saved_mixes_card_is_added_to_the_deck(self):
        response = self.client.get(self.saved_url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Retarget"], "#deck")
        self.assertEqual(response["HX-Reswap"], "beforeend")
        self.assertContains(response, "Saved Cosounds")
        self.assertContains(response, "Evening mix")
        self.assertContains(response, "closeThenLoad($event, mixes.find(mix => mix.id ===")
        self.assertContains(response, "card:removed")
        self.assertContains(response, "loadMix(mix)")
        self.assertContains(response, '"layers": [')
        self.assertContains(response, '"sound_title": "Night birds"')
        self.assertContains(response, "btn-error")
        self.assertContains(response, "1 layers")
        self.assertContains(response, 'hx-confirm="Delete this saved Cosound?"')
        self.assertContains(
            response,
            f'hx-post="{reverse("library:delete_mix", args=[SoundMix.objects.get(creator=self.user).pk])}"',
        )

    def test_non_htmx_requests_are_denied(self):
        for url in (self.liked_url, self.saved_url):
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Request Denied.")


class LibraryDeleteMixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="mix-owner", email="mix-owner@example.com"
        )
        cls.other_user = User.objects.create_user(
            username="other-listener", email="other-listener@example.com"
        )
        sound = Sound.objects.create(
            file="sounds/delete-mix.wav",
            title="Deletable sound",
            embeddings=[0, 0, 0, 0, 0],
        )
        cosound = Cosound.get_or_create_from_layers([(sound.pk, 0.5)])
        cls.sound_mix = SoundMix.objects.create(
            creator=cls.owner,
            cosound=cosound,
            title="Deletable mix",
        )

    def setUp(self):
        self.url = reverse("library:delete_mix", args=[self.sound_mix.pk])

    def test_owner_can_delete_a_saved_mix(self):
        self.client.force_login(self.owner)

        response = self.client.post(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SoundMix.objects.filter(pk=self.sound_mix.pk).exists())
        self.assertEqual(
            json.loads(response["HX-Trigger"]),
            {"mix-deleted": {"mixId": self.sound_mix.pk}},
        )

    def test_listener_cannot_delete_someone_elses_mix(self):
        self.client.force_login(self.other_user)

        response = self.client.post(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 404)
        self.assertTrue(SoundMix.objects.filter(pk=self.sound_mix.pk).exists())

    def test_delete_requires_login_and_an_htmx_post(self):
        response = self.client.post(self.url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 401)

        self.client.force_login(self.owner)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Request Denied.")
        self.assertTrue(SoundMix.objects.filter(pk=self.sound_mix.pk).exists())


class LibrarySearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sound1 = Sound.objects.create(
            file="sounds/rain.wav",
            title="Rainstorm",
            embeddings=[0, 0, 0, 0, 0],
        )
        cls.sound2 = Sound.objects.create(
            file="sounds/wind.wav",
            title="Gentle Wind",
            embeddings=[0, 0, 0, 0, 0],
        )

    def setUp(self):
        self.url = reverse("library:search")

    def test_search_filters_collection(self):
        response = self.client.get(f"{self.url}?q=Rain", HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rainstorm")
        self.assertContains(response, "await store.playAll()")
        self.assertNotContains(response, "Gentle Wind")


class LibraryCarouselTests(TestCase):
    def test_carousel_view_renders_default_view(self):
        url = reverse("library:carousel")
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="card-figure"')
        self.assertContains(response, 'id="card-body"')
