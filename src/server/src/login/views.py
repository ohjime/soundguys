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
    send_login_code,
)
from studio.utils import is_studio_url


def login_modal(request):

    if request.htmx:
        referer = request.headers.get("HX-Current-URL", "")
        if "/vote" in referer:
            request.session["post_login_partial"] = "vote/index.html#post_login"
        elif is_studio_url(referer) or request.GET.get("from") == "studio":
            # The referer covers /studio/ and studio.*; the query param covers
            # the studio gate embedded in the home page's STUDIO tab, where the
            # page URL alone no longer says "studio".
            request.session["post_login_partial"] = "studio/index.html#post_login"
        else:
            request.session.pop("post_login_partial", None)
        return show_modal(request, "login/index.html#modal")
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
            return render(
                request,
                "login/index.html#code_form",
                {"error": "Failed to send login code. Please try again later."},
            )
        return render(request, "login/index.html#code_form")

    email_errors = form.errors.get("email")
    return render(
        request,
        "login/index.html#code_form",
        {
            "error": (
                email_errors[0]
                if email_errors
                else "This email address can not be used with cosound."
            )
        },
    )


def verify_code(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")

    email, correct_code = get_login_state(request)

    if not email or not correct_code:
        return render(
            request,
            "login/index.html#code_form",
            {"error": "Login session expired. Please request a new code."},
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
                return render(
                    request,
                    "login/index.html#code_form",
                    {"error": "No account found for this email."},
                )

        return render(
            request,
            "login/index.html#code_form",
            {"error": "Invalid code. Please try again."},
        )

    return HttpResponse("Request Denied.")


def login_anonymously(request):
    if not request.htmx or request.method != "POST":
        return HttpResponse("Request Denied.")

    try:
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
