from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from login.views import login_anonymously, login_modal


class LoginModalTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _htmx_request(self, path, current_url="http://testserver/"):
        request = self.factory.get(path, HTTP_HX_CURRENT_URL=current_url)
        request.htmx = True
        request.session = {}
        request.user = AnonymousUser()
        return request

    def test_login_uses_the_shared_modal(self):
        request = self._htmx_request(
            "/login/?from=studio",
        )

        response = login_modal(request)

        self.assertEqual(response["HX-Retarget"], "#core_modal_content")
        self.assertEqual(response["HX-Reswap"], "innerHTML")
        self.assertEqual(response["HX-Trigger-After-Swap"], "show-modal")
        self.assertEqual(
            request.session["post_login_partial"],
            "studio/index.html#post_login",
        )
        self.assertContains(response, "Login")
        self.assertNotContains(response, "data-core-card")

    def test_vote_login_uses_the_modal_and_keeps_its_post_login_flow(self):
        request = self._htmx_request(
            "/login/",
            current_url="http://testserver/vote/",
        )

        response = login_modal(request)

        self.assertEqual(response["HX-Retarget"], "#core_modal_content")
        self.assertEqual(
            request.session["post_login_partial"],
            "vote/index.html#post_login",
        )

    def test_post_login_refreshes_the_header_with_the_user_avatar(self):
        request = self.factory.get("/")
        request.user = SimpleNamespace(
            is_authenticated=True,
            avatar_url="/media/test-avatar.png",
        )

        rendered = render_to_string(
            "login/index.html#post_login",
            request=request,
        )

        self.assertIn('id="core-header"', rendered)
        self.assertIn('hx-swap-oob="outerHTML"', rendered)
        self.assertIn('/media/test-avatar.png', rendered)

    def test_anonymous_login_closes_the_modal(self):
        request = self.factory.post("/login/anonymous/")
        request.htmx = True
        request.session = {}
        request.user = AnonymousUser()

        with self.settings(ROOT_URLCONF="config.urls"):
            from unittest.mock import patch

            with (
                patch("login.views.generate_anon_username", return_value="guest"),
                patch("login.views.generate_anon_email", return_value="guest@example.com"),
                patch("login.views.User.objects.create_user") as create_user,
                patch("login.views.Listener.objects.get_or_create"),
                patch("login.views.login"),
            ):
                user = create_user.return_value
                response = login_anonymously(request)

        trigger = response["HX-Trigger"]
        self.assertIn('"close-modal": true', trigger)
        self.assertIn('"auth-success": true', trigger)
        self.assertEqual(response["HX-Reswap"], "none")
