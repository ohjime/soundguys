from django.http import HttpResponse
from django.shortcuts import render

from core.models import Sound


def library_index(request):
    """The standalone library page; its c-core-loader fetches `library:initial`."""
    return render(request, "library/index.html")


def library_initial(request):
    """The library view content fragment served over htmx."""
    if not request.htmx:
        return HttpResponse("Request Denied.")
    sounds = (
        Sound.objects.select_related("artist")
        .prefetch_related("tags")
        .order_by("-created_at")[:24]
    )
    return render(
        request,
        "library/index.html#initial",
        {"sounds": sounds},
    )
