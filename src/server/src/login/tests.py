import time
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from login.views import (
    check_email,
    login_anonymously,
    login_modal,
    resend_code,
    verify_code,
)


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
        self.assertContains(response, "Continue Anonymously")

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

    def test_code_form_uses_six_digit_otp_component(self):
        rendered = render_to_string("login/index.html#code_form")

        self.assertIn('class="otp otp-sm sm:otp-lg otp-neutral"', rendered)
        self.assertIn('name="code"', rendered)
        self.assertIn('aria-label="6-digit verification code"', rendered)
        self.assertIn('maxlength="6"', rendered)
        self.assertIn('pattern="[0-9]{6}"', rendered)
        self.assertEqual(rendered.count("<span></span>"), 6)

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


class CodeFormRecoveryTests(SimpleTestCase):
    """The code step has to stay recoverable without a page reload.

    The daisyUI otp component makes its input pointer-events:none, hides the
    caret and selection, and only paints a focus ring while the value is valid,
    so a mistyped or pasted value used to leave the field looking inert and
    already full at maxlength — with the email form locked behind it.
    """

    def test_code_input_is_kept_digits_only_and_can_be_cleared(self):
        rendered = render_to_string("login/index.html#code_form")

        self.assertIn("replace(/[^0-9]/g, '')", rendered)
        self.assertIn("$refs.code.value = ''", rendered)
        self.assertIn(">Clear</button>", rendered)

    def test_code_input_takes_focus_after_the_htmx_swap(self):
        # autofocus does not fire for markup inserted by htmx.
        rendered = render_to_string("login/index.html#code_form")

        self.assertIn("$nextTick(() => $el.focus())", rendered)

    def test_code_form_offers_a_resend_and_a_labelled_escape(self):
        rendered = render_to_string("login/index.html#code_form")

        self.assertIn('hx-post="/login/resend-code/"', rendered)
        self.assertIn(">Resend code</span>", rendered)
        self.assertIn(">Use a different email</button>", rendered)

    def test_modal_submits_the_visible_email_field(self):
        # A readonly field is still serialised, so there is no hidden copy that
        # can go stale between an Alpine binding and htmx reading the form.
        rendered = render_to_string("login/index.html#modal")

        self.assertIn('name="email"', rendered)
        self.assertIn(':readonly="pressed"', rendered)
        self.assertNotIn('type="hidden"', rendered)
        self.assertNotIn("submittedEmail", rendered)


class ResendCodeTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, session):
        request = self.factory.post("/login/resend-code/")
        request.htmx = True
        request.session = session
        request.user = AnonymousUser()
        return request

    def test_resend_sends_a_fresh_code_to_the_session_email(self):
        request = self._request({"login_email": "a@b.com", "login_code": "123456"})

        with patch("login.views.send_login_code") as send:
            response = resend_code(request)

        send.assert_called_once_with(request, "a@b.com")
        self.assertContains(response, "A new code is on its way to a@b.com")

    def test_resend_is_throttled_without_reading_as_a_failure(self):
        # The throttle is what the complaint felt as "it wouldn't send me
        # another code", so it has to say the code in the inbox still works and
        # when a new one is available — as a notice, not a red error.
        request = self._request(
            {
                "login_email": "a@b.com",
                "login_code": "123456",
                "login_code_sent_at": time.time(),
            }
        )

        with patch("login.views.send_login_code") as send:
            response = resend_code(request)

        send.assert_not_called()
        self.assertContains(response, "The code already sent to a@b.com still works")
        self.assertContains(response, "alert-success")
        self.assertNotContains(response, "alert-error")
        self.assertContains(response, "Resend in ")

    def test_a_failed_resend_keeps_the_code_form_and_the_old_code(self):
        session = {
            "login_email": "a@b.com",
            "login_code": "123456",
            "login_code_sent_at": time.time() - 999,
        }
        request = self._request(session)

        with patch("login.views.send_login_code", side_effect=Exception("smtp")):
            response = resend_code(request)

        self.assertContains(response, "Failed to send login code.")
        self.assertContains(response, 'name="code"')
        self.assertEqual(session["login_code"], "123456")

    def test_resend_without_a_login_session_hands_back_the_email_field(self):
        request = self._request({})

        with patch("login.views.send_login_code") as send:
            response = resend_code(request)

        send.assert_not_called()
        self.assertContains(response, "Login session expired.")
        self.assertEqual(response["HX-Trigger"], "login-cancel")
        self.assertNotContains(response, 'name="code"')

    def test_resend_rejects_non_htmx_requests(self):
        request = self._request({"login_email": "a@b.com", "login_code": "123456"})
        request.htmx = False

        with patch("login.views.send_login_code") as send:
            response = resend_code(request)

        send.assert_not_called()
        self.assertContains(response, "Request Denied.")


class DeadEndRecoveryTests(SimpleTestCase):
    """Any state with no live code must unlock the email field.

    The email input goes readonly the moment a code is requested, so a dead end
    that still renders six empty boxes leaves the user with nothing to type into
    and nothing to submit — the reload the complaint had to resort to.
    """

    def setUp(self):
        self.factory = RequestFactory()

    def _htmx_post(self, path, data=None, session=None):
        request = self.factory.post(path, data or {})
        request.htmx = True
        request.session = {} if session is None else session
        request.user = AnonymousUser()
        return request

    def test_a_rejected_email_unlocks_the_field_instead_of_asking_for_a_code(self):
        request = self._htmx_post("/login/check-email/", {"email": "not-an-email"})

        response = check_email(request)

        self.assertEqual(response["HX-Trigger"], "login-cancel")
        self.assertNotContains(response, 'name="code"')
        self.assertNotContains(response, ">Verify<")
        self.assertContains(response, "editable again")

    def test_a_failed_send_unlocks_the_field_and_leaves_no_stale_code(self):
        session = {"login_code": "999999", "login_email": "old@b.com"}
        request = self._htmx_post("/login/check-email/", {"email": "a@b.com"}, session)

        with (
            patch("login.views.UnifiedRequestLoginCodeForm") as form_cls,
            patch("login.views.send_login_code", side_effect=Exception("smtp")),
        ):
            form_cls.return_value.is_valid.return_value = True
            form_cls.return_value.cleaned_data = {"email": "a@b.com"}
            response = check_email(request)

        self.assertEqual(response["HX-Trigger"], "login-cancel")
        self.assertContains(response, "Failed to send login code.")
        self.assertEqual(session, {})

    def test_a_sent_code_starts_the_resend_countdown(self):
        session = {}
        request = self._htmx_post("/login/check-email/", {"email": "a@b.com"}, session)

        with (
            patch("login.views.UnifiedRequestLoginCodeForm") as form_cls,
            patch("login.views.send_login_code") as send,
        ):
            form_cls.return_value.is_valid.return_value = True
            form_cls.return_value.cleaned_data = {"email": "a@b.com"}
            send.side_effect = lambda req, email: req.session.update(
                {"login_code": "123456", "login_email": email,
                 "login_code_sent_at": time.time()}
            )
            response = check_email(request)

        self.assertContains(response, 'name="code"')
        self.assertContains(response, "Resend in ")
        self.assertContains(response, "disabled")

    def test_verify_without_a_login_session_unlocks_the_email_field(self):
        request = self._htmx_post("/login/verify-code/", {"code": "123456"})

        response = verify_code(request)

        self.assertEqual(response["HX-Trigger"], "login-cancel")
        self.assertNotContains(response, 'name="code"')

    def test_a_wrong_code_keeps_the_form_so_it_can_be_retyped(self):
        request = self._htmx_post(
            "/login/verify-code/",
            {"code": "000000"},
            {"login_email": "a@b.com", "login_code": "123456"},
        )

        response = verify_code(request)

        self.assertContains(response, "Invalid code.")
        self.assertContains(response, 'name="code"')
        self.assertNotIn("HX-Trigger", response)
