from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import User


TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


class ProfileFeatureTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory()
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
            MEDIA_URL="/media/",
            STORAGES=TEST_STORAGES,
        )
        self.settings_override.enable()
        self.user = User.objects.create_user(
            email="listener@example.com",
            username="Listener",
            password="password",
        )
        self.url = reverse("profile:profile_modal")
        self.client.force_login(self.user)

    def tearDown(self):
        self.settings_override.disable()
        self.media_directory.cleanup()

    def _image(self, name="avatar.png", image_format="PNG", size=(2, 2)):
        content = BytesIO()
        Image.new("RGB", size, color=(30, 100, 140)).save(content, format=image_format)
        return SimpleUploadedFile(
            name,
            content.getvalue(),
            content_type=f"image/{image_format.lower()}",
        )

    def test_authenticated_htmx_get_opens_the_shared_modal(self):
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Retarget"], "#core_modal_content")
        self.assertEqual(response["HX-Reswap"], "innerHTML")
        self.assertEqual(response["HX-Trigger-After-Swap"], "show-modal")
        self.assertContains(response, "Your profile")
        self.assertContains(response, 'value="Listener"')
        self.assertContains(response, 'enctype="multipart/form-data"')

    def test_non_htmx_and_anonymous_requests_are_denied(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

        self.client.logout()
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            self.url,
            {"username": "Intruder"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 403)

    def test_valid_post_updates_only_the_current_user_and_refreshes_header(self):
        other_user = User.objects.create_user(
            email="other@example.com",
            username="Other",
            password="password",
        )

        response = self.client.post(
            self.url,
            {"username": "  NewListener  ", "user_id": other_user.pk},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        other_user.refresh_from_db()
        self.assertEqual(self.user.username, "NewListener")
        self.assertEqual(other_user.username, "Other")
        self.assertEqual(response["HX-Trigger"], "close-modal")
        self.assertEqual(response["HX-Reswap"], "none")
        self.assertContains(response, 'id="core-header"')
        self.assertContains(response, 'hx-swap-oob="outerHTML"')
        self.assertContains(response, "Edit profile for NewListener")

    def test_username_must_be_unique_ignoring_case(self):
        User.objects.create_user(
            email="taken@example.com",
            username="TakenName",
            password="password",
        )

        response = self.client.post(
            self.url,
            {"username": "takenname"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That username is already in use.")
        self.assertEqual(response["HX-Trigger-After-Swap"], "show-modal")
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "Listener")

    def test_empty_post_is_bound_and_rejected(self):
        response = self.client.post(self.url, {}, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "Listener")

    def test_valid_picture_upload_updates_the_avatar(self):
        response = self.client.post(
            self.url,
            {"username": "Listener", "avatar": self._image()},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar.name.startswith("avatars/avatar"))
        self.assertContains(response, self.user.avatar.url)

    def test_invalid_or_oversized_picture_is_rejected(self):
        invalid_image = SimpleUploadedFile(
            "avatar.png",
            b"not an image",
            content_type="image/png",
        )
        response = self.client.post(
            self.url,
            {"username": "Listener", "avatar": invalid_image},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "Upload a valid image")

        oversized_image = self._image(
            name="avatar.bmp",
            image_format="BMP",
            size=(1024, 1024),
        )
        response = self.client.post(
            self.url,
            {"username": "Listener", "avatar": oversized_image},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "Choose an image smaller than 3 MB.")
        self.user.refresh_from_db()
        self.assertFalse(self.user.avatar)

    def test_avatar_can_be_reset_to_the_generated_picture(self):
        self.user.avatar = self._image()
        self.user.save(update_fields=["avatar"])
        self.assertTrue(self.user.avatar)

        response = self.client.post(
            self.url,
            {"username": "Listener", "avatar-clear": "on"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.avatar)
        self.assertContains(response, self.user.avatar_url.replace("&", "&amp;"))

    def test_invalid_post_keeps_the_avatar_reset_choice_visible(self):
        self.user.avatar = self._image()
        self.user.save(update_fields=["avatar"])
        User.objects.create_user(
            email="taken@example.com",
            username="TakenName",
            password="password",
        )

        response = self.client.post(
            self.url,
            {"username": "takenname", "avatar-clear": "on"},
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'name="avatar-clear"')
        self.assertContains(response, "checked")
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar)

    def test_header_links_authenticated_users_to_profile_only(self):
        factory = RequestFactory()
        request = factory.get("/")
        request.user = self.user

        rendered = render_to_string("cotton/core_header.html", request=request)
        self.assertIn(f'hx-get="{self.url}"', rendered)
        self.assertIn("Edit profile for Listener", rendered)

        request.user = AnonymousUser()
        rendered = render_to_string("cotton/core_header.html", request=request)
        self.assertNotIn(f'hx-get="{self.url}"', rendered)
        self.assertIn(f'hx-get="{reverse("login:login_modal")}"', rendered)

    def test_profile_route_is_available_on_the_studio_urlconf(self):
        self.assertEqual(
            reverse("profile:profile_modal", urlconf="config.urls_studio"),
            "/profile/",
        )
