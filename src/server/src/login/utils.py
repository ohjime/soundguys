import math
import time

from allauth.account.adapter import get_adapter
from random_username.generate import generate_username

ANON_EMAIL_DOMAIN = "anon.cosound.ca"

# Resending is the only way back to a fresh code, so it has to stay cheap for a
# stuck user without turning the endpoint into a mail cannon.
RESEND_COOLDOWN_SECONDS = 30


def send_login_code(request, email):
    adapter = get_adapter(request)
    code = adapter.generate_login_code()
    adapter.send_mail("account/email/login_code", email, {"code": code})
    request.session["login_code"] = code
    request.session["login_email"] = email
    request.session["login_code_sent_at"] = time.time()


def get_login_state(request):
    return (
        request.session.get("login_email"),
        request.session.get("login_code"),
    )


def seconds_until_resend_allowed(request):
    """Seconds left on the resend cooldown; 0 when a new code may be sent."""
    sent_at = request.session.get("login_code_sent_at")
    if not sent_at:
        return 0
    return max(0, math.ceil(RESEND_COOLDOWN_SECONDS - (time.time() - sent_at)))


def clear_login_state(request):
    request.session.pop("login_code", None)
    request.session.pop("login_email", None)
    request.session.pop("login_code_sent_at", None)


def generate_anon_username():
    """Return a single random username (e.g. 'BraveOwl42')."""
    return generate_username(1)[0]


def generate_anon_email(username):
    """Return an @anon.cosound.ca email tied to the given username."""
    return f"{username}@{ANON_EMAIL_DOMAIN}"


def is_anonymous_user(user):
    """A user is 'anonymous' if it was minted via the guest login flow."""
    if user.is_anonymous:
        return True
    if user.is_authenticated and user.email:
        return user.email.endswith(f"@{ANON_EMAIL_DOMAIN}")
    return False


