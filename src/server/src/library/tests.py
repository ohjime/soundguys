import json

from django.test import TestCase
from django.urls import reverse
from django.utils import formats
from django.utils.timezone import localtime

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
    """The picker opens on tags, not on sounds."""

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
        cls.sound.tags.add("rain")
        cls.other = Sound.objects.create(
            file="sounds/traffic.wav",
            title="Ring Road",
            embeddings=[0, 0, 0, 0, 0],
        )
        cls.other.tags.add("traffic")

    def setUp(self):
        self.url = reverse("library:swap")

    def get(self):
        return self.client.get(self.url, HTTP_HX_REQUEST="true")

    def test_non_htmx_request_denied(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Request Denied.")

    def test_opens_on_tag_buttons_rather_than_sounds(self):
        response = self.get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rain")
        self.assertContains(response, 'for="tag-filter-0"')
        self.assertNotContains(response, "Forest Rain")

    def test_picker_frame_keeps_the_cancel_button(self):
        response = self.get()

        self.assertContains(response, 'id="cancel-swap-button-list"')
        self.assertContains(response, 'id="sound-search-results"')
        self.assertContains(response, "Cancel")

    def test_filter_row_starts_as_the_search_label(self):
        """Idle, the row is the label; the chips are present but collapsed.

        Which arm shows is CSS (`.tag-filter` in main.css), so the markup can
        only assert that both are rendered and that the chips are the ones the
        tag buttons point at.
        """
        response = self.get()

        self.assertContains(response, "Search Collected Sounds")
        self.assertContains(response, 'class="tag-filter-label"')
        self.assertContains(response, 'id="sound-tag-filter"')
        self.assertContains(response, 'class="filter w-max"')
        self.assertContains(response, 'type="reset"')

    def test_filter_row_is_contained_and_scrollable(self):
        response = self.get()

        self.assertContains(response, 'class="min-w-0 flex-1 overflow-x-auto"')

    def test_tag_buttons_wear_their_own_seeded_artwork(self):
        awkward = Sound.objects.create(
            file="sounds/awkward.wav",
            title="Awkward",
            embeddings=[0, 0, 0, 0, 0],
        )
        awkward.tags.add("field / outdoor")

        response = self.get()

        self.assertContains(
            response,
            "background-image: url('https://api.dicebear.com/10.x/waves/svg?seed=rain')",
        )
        # A tag with a slash and spaces has to survive as one seed, or the
        # button ends up asking DiceBear for a path that is not there.
        self.assertContains(response, "seed=field%20%2F%20outdoor")

    def test_every_tag_button_has_a_radio_to_check(self):
        response = self.get()
        body = response.content.decode()

        for index in range(2):
            self.assertIn(f'for="tag-filter-{index}"', body)
            self.assertIn(f'id="tag-filter-{index}"', body)
        self.assertNotIn('for="tag-filter-2"', body)

    def test_anonymous_listener_sees_the_catalogues_tags(self):
        response = self.get()

        self.assertContains(response, "rain")
        self.assertContains(response, "traffic")

    def test_tags_come_from_what_the_listener_collected(self):
        listener = Listener.objects.create(user=self.user)
        listener.collection.add(self.sound)
        self.client.force_login(self.user)

        response = self.get()

        self.assertContains(response, "rain")
        self.assertNotContains(response, "traffic")

    def test_tags_come_from_saved_mixes_too(self):
        listener = Listener.objects.create(user=self.user)
        listener.collection.add(self.sound)
        cosound = Cosound.get_or_create_from_layers([(self.other.pk, 1)])
        SoundMix.objects.create(creator=self.user, cosound=cosound, title="Commute")
        self.client.force_login(self.user)

        response = self.get()

        self.assertContains(response, "rain")
        self.assertContains(response, "traffic")

    def test_counts_are_what_pressing_the_tag_will_find(self):
        listener = Listener.objects.create(user=self.user)
        listener.collection.add(self.sound)
        for index in range(2):
            extra = Sound.objects.create(
                file=f"sounds/more-rain-{index}.wav",
                title=f"More rain {index}",
                embeddings=[0, 0, 0, 0, 0],
            )
            extra.tags.add("rain")
        self.client.force_login(self.user)

        response = self.get()

        # One collected, two never seen — the button promises all three,
        # because that is what the tag filter returns.
        self.assertContains(response, "3 sounds")

    def test_untagged_collection_falls_back_to_the_catalogue(self):
        bare = Sound.objects.create(
            file="sounds/bare.wav",
            title="Untagged",
            embeddings=[0, 0, 0, 0, 0],
        )
        listener = Listener.objects.create(user=self.user)
        listener.collection.add(bare)
        self.client.force_login(self.user)

        response = self.get()

        self.assertContains(response, "rain")
        self.assertContains(response, "traffic")


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
        self.assertContains(response, "Favourited Sounds")
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
        cls.sound1.tags.add("weather")
        cls.sound2 = Sound.objects.create(
            file="sounds/wind.wav",
            title="Gentle Wind",
            embeddings=[0, 0, 0, 0, 0],
        )
        cls.sound2.tags.add("weather")
        cls.sound3 = Sound.objects.create(
            file="sounds/market.wav",
            title="Market Square",
            embeddings=[0, 0, 0, 0, 0],
        )
        cls.sound3.tags.add("voices")

    def setUp(self):
        self.url = reverse("library:search")

    def get(self, query):
        return self.client.get(f"{self.url}?{query}", HTTP_HX_REQUEST="true")

    def test_search_filters_collection(self):
        response = self.get("q=Rain")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rainstorm")
        self.assertContains(response, "await store.playAll()")
        self.assertNotContains(response, "Gentle Wind")

    def test_rows_shimmer_until_their_artwork_arrives(self):
        response = self.get("q=Rain")

        self.assertContains(response, 'class="skeleton absolute inset-0"')
        self.assertContains(response, "artLoaded: false")
        # The skeleton leaves on a transition rather than blinking out, so the
        # two halves cross-fade instead of cutting.
        self.assertContains(
            response, 'x-transition:leave="transition-opacity duration-300"'
        )
        # A cached image is already `complete` before Alpine runs and never
        # fires `load`, so without this the skeleton would never clear.
        self.assertContains(response, 'x-init="if ($el.complete) revealArt()"')
        self.assertContains(response, '@load="revealArt()"')
        self.assertContains(response, '@error="revealArt()"')
        # Two frames, so the browser paints opacity-0 before the flag flips —
        # otherwise a cached image pops instead of fading.
        self.assertContains(
            response, "requestAnimationFrame(() => requestAnimationFrame("
        )

    def test_a_tag_narrows_the_list(self):
        response = self.get("tag=weather")

        self.assertContains(response, "Rainstorm")
        self.assertContains(response, "Gentle Wind")
        self.assertNotContains(response, "Market Square")

    def test_a_tag_and_a_query_compose(self):
        response = self.get("tag=weather&q=Wind")

        self.assertContains(response, "Gentle Wind")
        self.assertNotContains(response, "Rainstorm")
        self.assertNotContains(response, "Market Square")

    def test_clearing_both_brings_the_tag_buttons_back(self):
        response = self.get("q=&tag=")

        self.assertContains(response, 'for="tag-filter-0"')
        self.assertContains(response, "weather")
        self.assertNotContains(response, "Rainstorm")

    def test_non_htmx_request_denied(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Request Denied.")


class LibraryCarouselTests(TestCase):
    def test_carousel_view_renders_default_view(self):
        url = reverse("library:carousel")
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="card-figure"')
        self.assertContains(response, 'id="card-body"')

    def test_the_artist_name_is_a_link_only_when_they_have_a_page(self):
        """The card decides per layer, in the browser.

        An artist who gave us a page is reached at it, in a new tab, because
        leaving in this one would stop the mix the card is playing. An artist
        without one is a plain name: we host no page of our own to stand in.
        """
        response = self.client.get(
            reverse("library:carousel"), HTTP_HX_REQUEST="true"
        )

        self.assertContains(
            response, ':href="$store.soundLayers.currentLayer?.artist_url"'
        )
        self.assertContains(response, 'target="_blank"')
        self.assertContains(
            response, 'x-show="!$store.soundLayers.currentLayer?.artist_url"'
        )
        # Nothing opens a modal over a credit any more.
        self.assertNotContains(response, "artist/details")
        self.assertNotContains(response, "core_modal_content")


class LibrarySaveTests(TestCase):
    """Naming a mix, and what happens when the name is already taken.

    The title box is how a listener edits a saved Cosound: load it, change a
    layer, save. Keeping the name replaces what was there, typing a new one
    keeps both — so the save path has to tell those two apart and ask before it
    does the destructive one.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="saver", email="saver@example.com", password="password"
        )
        cls.sound = Sound.objects.create(
            file="sounds/save-a.wav",
            title="Rain on tin",
            embeddings=[0, 0, 0, 0, 0],
        )
        cls.other_sound = Sound.objects.create(
            file="sounds/save-b.wav",
            title="Distant thunder",
            embeddings=[0, 0, 0, 0, 0],
        )
        cls.original = SoundMix.objects.create(
            creator=cls.user,
            cosound=Cosound.get_or_create_from_layers([(cls.sound.pk, 1)]),
            title="Storm",
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.save_url = reverse("library:save")
        self.confirm_url = reverse("library:save_confirm")

    def _layers(self, *sounds):
        return json.dumps(
            [{"sound_id": sound.pk, "sound_gain": 1} for sound in sounds]
        )

    def _tweaked(self):
        """The saved mix with a second layer — same name, different Cosound."""
        return self._layers(self.sound, self.other_sound)

    def test_dialog_opens_on_the_name_the_mix_already_has(self):
        response = self.client.post(
            self.save_url,
            {"layers": self._layers(self.sound)},
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'value="Storm"')

    def test_dialog_falls_back_to_the_posted_title_for_an_unsaved_mix(self):
        response = self.client.post(
            self.save_url,
            {"layers": self._tweaked(), "title": "Storm"},
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'value="Storm"')

    def test_the_dialog_does_not_grab_the_cursor(self):
        """A prefilled name is a decision already made; Save is the answer.

        The X is the way out of it instead — and it is only there when there is
        something to clear.
        """
        prefilled = self.client.post(
            self.save_url,
            {"layers": self._layers(self.sound)},
            HTTP_HX_REQUEST="true",
        )
        self.assertNotContains(prefilled, "autofocus")
        self.assertContains(prefilled, 'aria-label="Clear the name"')

        empty = self.client.post(
            self.save_url,
            {"layers": self._layers(self.other_sound)},
            HTTP_HX_REQUEST="true",
        )
        self.assertNotContains(empty, "autofocus")
        self.assertNotContains(empty, 'aria-label="Clear the name"')

    def test_the_box_is_a_validator_that_waits_to_be_touched(self):
        response = self.client.post(
            self.save_url,
            {"layers": self._layers(self.other_sound)},
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "input validator")
        self.assertContains(response, "validator-hint")

    def test_the_dialog_shows_when_this_mix_was_last_saved(self):
        response = self.client.post(
            self.save_url,
            {"layers": self._layers(self.sound)},
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "Last saved")
        self.assertContains(
            response, formats.date_format(localtime(self.original.updated_at), "M j, Y")
        )

    def test_a_mix_with_no_row_of_its_own_has_no_time_to_show(self):
        """The name can be a suggestion; the timestamp never is.

        Tweaked layers are not in the table yet, and neither is a mix opened on
        an Explore post's title — so there is nothing to date.
        """
        tweaked = self.client.post(
            self.save_url,
            {"layers": self._tweaked(), "title": "Storm"},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(tweaked, 'value="Storm"')
        self.assertNotContains(tweaked, "Last saved")

    def test_the_overwrite_warning_dates_the_cosound_it_would_replace(self):
        response = self.client.post(
            self.confirm_url,
            {"layers": self._tweaked(), "title": "Storm"},
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "Last saved")
        self.assertContains(
            response, formats.date_format(localtime(self.original.updated_at), "M j, Y")
        )

    def test_the_title_is_bold_when_it_is_filled_and_grey_when_it_is_not(self):
        filled = self.client.post(
            self.save_url,
            {"layers": self._layers(self.sound)},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(filled, "title.trim() ? 'font-bold' : 'font-normal'")
        self.assertContains(filled, 'x-data="{ title: \'Storm\' }"')

        empty = self.client.post(
            self.save_url,
            {"layers": self._layers(self.other_sound)},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(empty, 'placeholder="Empty"')
        self.assertContains(empty, "placeholder:text-base-content/40")
        self.assertContains(empty, 'x-data="{ title: \'\' }"')

    def test_saving_a_new_name_keeps_both_cosounds(self):
        response = self.client.post(
            self.confirm_url,
            {"layers": self._tweaked(), "title": "Storm II"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted(
                SoundMix.objects.filter(creator=self.user).values_list(
                    "title", flat=True
                )
            ),
            ["Storm", "Storm II"],
        )

    def test_reusing_the_name_asks_before_it_replaces_anything(self):
        response = self.client.post(
            self.confirm_url,
            {"layers": self._tweaked(), "title": "Storm"},
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "Overwrite")
        self.assertEqual(response["HX-Trigger-After-Swap"], "show-modal")
        # Nothing written yet: the old Storm is still the only one.
        self.assertEqual(SoundMix.objects.filter(creator=self.user).count(), 1)
        self.assertEqual(
            SoundMix.objects.get(pk=self.original.pk).cosound_id,
            self.original.cosound_id,
        )

    def test_confirming_the_overwrite_replaces_the_old_cosound(self):
        response = self.client.post(
            self.confirm_url,
            {"layers": self._tweaked(), "title": "Storm", "overwrite": "1"},
            HTTP_HX_REQUEST="true",
        )

        mixes = SoundMix.objects.filter(creator=self.user)
        self.assertEqual(mixes.count(), 1)
        saved = mixes.get()
        self.assertEqual(saved.title, "Storm")
        self.assertNotEqual(saved.cosound_id, self.original.cosound_id)
        self.assertFalse(SoundMix.objects.filter(pk=self.original.pk).exists())

        triggers = json.loads(response["HX-Trigger"])
        self.assertEqual(triggers["mix-deleted"], {"mixId": self.original.pk})
        self.assertEqual(triggers["mix-titled"], {"title": "Storm"})

    def test_resaving_the_same_mix_under_its_own_name_never_asks(self):
        response = self.client.post(
            self.confirm_url,
            {"layers": self._layers(self.sound), "title": "Storm"},
            HTTP_HX_REQUEST="true",
        )

        self.assertNotContains(response, "Overwrite")
        self.assertEqual(SoundMix.objects.filter(creator=self.user).count(), 1)

    def test_renaming_a_mix_does_not_collide_with_another_listener(self):
        stranger = User.objects.create_user(
            username="stranger", email="stranger@example.com"
        )
        SoundMix.objects.create(
            creator=stranger,
            cosound=Cosound.get_or_create_from_layers([(self.other_sound.pk, 1)]),
            title="Storm",
        )

        response = self.client.post(
            self.confirm_url,
            {"layers": self._tweaked(), "title": "Squall"},
            HTTP_HX_REQUEST="true",
        )

        self.assertNotContains(response, "Overwrite")
        self.assertEqual(SoundMix.objects.filter(creator=stranger).count(), 1)

    def test_a_rename_tells_the_transport_the_new_name(self):
        response = self.client.post(
            self.confirm_url,
            {"layers": self._layers(self.sound), "title": "Squall"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(
            json.loads(response["HX-Trigger"])["mix-titled"], {"title": "Squall"}
        )
