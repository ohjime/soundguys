import json

from django.contrib.auth import login, logout
from django.http import HttpResponse, Http404
from django.shortcuts import render

from core.models import Listener, User
from core.utils import close_modal, show_modal
from login.adapters import UnifiedRequestLoginCodeForm
from login.utils import (
    clear_login_state,
    generate_anon_email,
    generate_anon_username,
    get_login_state,
    seconds_until_resend_allowed,
    send_login_code,
)
from studio.utils import is_studio_url


def _code_form(request, **context):
    """Render the code step, always telling it how long resend stays locked."""
    context.setdefault("resend_wait", seconds_until_resend_allowed(request))
    return render(request, "login/index.html#code_form", context)


def _restart_login(request, error):
    """Render a dead end and hand the email field back to the user.

    Every caller here means there is no code left to type, so the modal must
    stop showing six empty boxes above an email field it has locked. htmx fires
    HX-Trigger before the swap, while the form listening for `login-cancel` is
    still on the page, so the field is editable again by the time the message
    lands.
    """
    clear_login_state(request)
    response = render(request, "login/index.html#code_error", {"error": error})
    response["HX-Trigger"] = "login-cancel"
    return response


def login_modal(request):

    if request.htmx:
        referer = request.headers.get("HX-Current-URL", "")
        is_vote = "/vote" in referer
        if is_vote:
            request.session["post_login_partial"] = "vote/index.html#post_login"
        elif is_studio_url(referer) or request.GET.get("from") == "studio":
            # The referer covers /studio/ and studio.*; the query param covers
            # the studio gate embedded in the home page's STUDIO tab, where the
            # page URL alone no longer says "studio".
            request.session["post_login_partial"] = "studio/index.html#post_login"
        else:
            request.session.pop("post_login_partial", None)
        return show_modal(
            request, "login/index.html#modal", {"allow_anonymous": is_vote}
        )
    return Http404("Page not found.")


def check_email(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")

    form = UnifiedRequestLoginCodeForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        try:
            send_login_code(request, email)
        except Exception:
            return _restart_login(
                request, "Failed to send login code. Please try again later."
            )
        return _code_form(request)

    email_errors = form.errors.get("email")
    return _restart_login(
        request,
        email_errors[0]
        if email_errors
        else "This email address can not be used with cosound.",
    )


def resend_code(request):
    """Send a fresh code to the address already held in the login session.

    The email form locks itself once a code is out, so without this the only
    route back to a working code is reloading the page.
    """
    if not request.htmx or request.method != "POST":
        return HttpResponse("Request Denied.")

    email, _ = get_login_state(request)

    if not email:
        return _restart_login(
            request, "Login session expired. Please request a new code."
        )

    wait = seconds_until_resend_allowed(request)
    if wait:
        # Not a failure: the code already in their inbox still verifies. Say so,
        # in the calm colour, or the throttle reads as the login being broken.
        return _code_form(
            request,
            notice=(
                f"The code already sent to {email} still works. "
                f"You can request a new one in {wait} seconds."
            ),
        )

    try:
        send_login_code(request, email)
    except Exception:
        # The previous code is untouched by a failed send, so keep the form.
        return _code_form(
            request, error="Failed to send login code. Please try again later."
        )

    return _code_form(request, notice=f"A new code is on its way to {email}.")


def verify_code(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")

    email, correct_code = get_login_state(request)

    if not email or not correct_code:
        return _restart_login(
            request, "Login session expired. Please request a new code."
        )

    if request.method == "POST":
        input_code = request.POST.get("code", "").strip()

        if input_code == str(correct_code):
            try:
                user = User.objects.get(email__iexact=email)
                Listener.objects.get_or_create(user=user)
                login(
                    request, user, backend="django.contrib.auth.backends.ModelBackend"
                )
                clear_login_state(request)
                post_login_partial = request.session.pop(
                    "post_login_partial", "login/index.html#post_login"
                )
                response = close_modal(request, template=post_login_partial)
                response["HX-Trigger"] = json.dumps(
                    {"close-modal": True, "auth-success": True}
                )
                return response
            except User.DoesNotExist:
                # The code was right, so retyping it cannot help — send them
                # back to the email field rather than to six empty boxes.
                return _restart_login(request, "No account found for this email.")

        return _code_form(request, error="Invalid code. Please try again.")

    return HttpResponse("Request Denied.")


def _authenticate_anonymously(request):
    """Create the guest account used by the login modal and sign it in."""
    username = generate_anon_username()
    user = User.objects.create_user(
        username=username,
        email=generate_anon_email(username),
        password=None,
    )
    user.set_unusable_password()
    user.save()
    Listener.objects.get_or_create(user=user)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")


def login_anonymously(request):
    if not request.htmx or request.method != "POST":
        return HttpResponse("Request Denied.")

    try:
        _authenticate_anonymously(request)
    except Exception:
        return show_modal(
            request,
            "core/modal_alert.html",
            {
                "alert_msg": "Failed to continue anonymously. Please try again later.",
                "alert_type": "alert-warning",
            },
        )

    post_login_partial = request.session.pop(
        "post_login_partial", "login/index.html#post_login"
    )
    response = close_modal(request, template=post_login_partial)
    response["HX-Trigger"] = json.dumps(
        {"close-modal": True, "auth-success": True}
    )
    return response


def cancel_code(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")

    clear_login_state(request)
    return HttpResponse("")


def logout_modal(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")
    return show_modal(request, "login/index.html#logout_modal")


def perform_logout(request):
    if not request.htmx or request.method != "POST":
        return HttpResponse("Request Denied.")
    logout(request)
    response = HttpResponse("")
    response["HX-Refresh"] = "true"
    return response
