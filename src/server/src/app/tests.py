from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse


class AppNavigationTabsTests(TestCase):
    def test_home_page_renders_library_tab(self):
        response = self.client.get(reverse("app:home_initial"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="LIBRARY"')
        self.assertContains(response, reverse("library:initial"))

    def test_navigation_waits_for_the_whole_outgoing_tab_to_exit(self):
        response = self.client.get(reverse("app:home_initial"), HTTP_HX_REQUEST="true")

        self.assertContains(response, "data-swap-height")
        self.assertContains(response, 'hx-swap="innerHTML swap:200ms"', count=3)
        self.assertContains(response, 'hx-target="#tab_content"', count=3)
        self.assertContains(response, 'hx-sync="#tab_content:replace"', count=3)
        self.assertContains(response, 'aria-controls="tab_content"', count=3)
        self.assertContains(response, "htmx-swapping]:translate-y-4")
        self.assertContains(response, "htmx-swapping]:opacity-0")


class AppTabBodyTests(TestCase):
    def test_mixer_tab_renders_started_primary_and_secondary_content(self):
        preserved_mix = {
            "id": 7,
            "title": "Preserved Mix",
            "created_at": "2026-08-19T00:00:00+00:00",
            "layers": [],
        }
        with (
            patch("app.views.get_random_sounds", return_value=[]) as get_sounds,
            patch(
                "app.views.serialize_user_mixes", return_value=[preserved_mix]
            ) as serialize_mixes,
        ):
            response = self.client.get(
                reverse("app:home_tab_mixer"), HTTP_HX_REQUEST="true"
            )

        get_sounds.assert_called_once()
        serialize_mixes.assert_called_once()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-core-tab-body")
        self.assertContains(response, 'data-reveal-mode="started"')
        self.assertContains(response, "data-tab-primary")
        self.assertContains(response, "data-tab-secondary")
        self.assertContains(response, "data-cosound-mixer-root")
        self.assertContains(response, "$store.soundLayers?.started")
        self.assertContains(response, "Your Mixes")
        self.assertContains(response, "Preserved Mix")
        content = response.content.decode()
        self.assertLess(
            content.index("data-tab-primary"),
            content.index("data-cosound-mixer-root"),
        )
        self.assertLess(
            content.index("data-cosound-mixer-root"),
            content.index("data-tab-secondary"),
        )
        self.assertLess(
            content.index("data-tab-secondary"), content.index("Your Mixes")
        )

    def test_about_tab_renders_timed_primary_and_secondary_content(self):
        response = self.client.get(
            reverse("app:home_tab_about"), HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-reveal-mode="timer"')
        self.assertContains(response, 'data-reveal-delay="1800"')
        self.assertContains(response, "data-tab-primary", count=1)
        self.assertContains(response, "data-tab-secondary", count=1)
        self.assertContains(response, "Sound, chosen by the room")
        self.assertContains(response, "Two sensors, two different signals")
        self.assertContains(response, "Read In-Depth Technical Details ↓")
        content = response.content.decode()
        self.assertLess(
            content.index("data-tab-primary"),
            content.index("Sound, chosen by the room"),
        )
        self.assertLess(
            content.index("Sound, chosen by the room"),
            content.index("data-tab-secondary"),
        )
        self.assertLess(
            content.index("data-tab-secondary"),
            content.index("Two sensors, two different signals"),
        )
