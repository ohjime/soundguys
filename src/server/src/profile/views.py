from django.http import HttpResponse, HttpResponseNotAllowed

from core.utils import close_modal, get_random_avatar_url, show_modal
from profile.forms import ProfileForm


def profile_modal(request):
    if not getattr(request, "htmx", False) or not request.user.is_authenticated:
        return HttpResponse("Request Denied.", status=403)
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])

    default_avatar_url = get_random_avatar_url(request.user.pk)
    current_avatar_url = request.user.avatar_url
    has_custom_avatar = bool(request.user.avatar)
    clear_avatar_requested = request.method == "POST" and "avatar-clear" in request.POST
    form = ProfileForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        instance=request.user,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        return close_modal(request, "profile/index.html#post_update")

    return show_modal(
        request,
        "profile/index.html#modal",
        {
            "form": form,
            "avatar_url": current_avatar_url,
            "preview_avatar_url": (
                default_avatar_url if clear_avatar_requested else current_avatar_url
            ),
            "default_avatar_url": default_avatar_url,
            "has_custom_avatar": has_custom_avatar,
            "clear_avatar_requested": clear_avatar_requested,
        },
    )
