"""URLconf for the studio subdomain (studio.cosound.ca).

Mounts the artist studio at the site root so the card creator lives at
``studio.cosound.ca/`` instead of ``cosound.ca/studio/``. Selected per request by
``config.middleware.SubdomainURLConf`` based on the Host header; the default
``config.urls`` (studio at ``/studio/``) is untouched, so local dev and the
apex/www site are unaffected.

``login/`` is included because the studio gates on being a signed-in artist and
renders the shared login card, whose htmx endpoints must resolve on this host —
the url tag reverses against the per-request urlconf, not ROOT_URLCONF.

``mixer/`` is included for the same reason: saving is the one thing a studio mix
and a listener's mix do identically (a list of sound ids and gains), so the
builder's transport posts to ``mixer:save``, and the title modal it opens posts
to ``mixer:save_confirm``. Both have to resolve here.

``profile/`` keeps the shared authenticated header's account editor available
on the studio host as well as the main site.
"""

from django.urls import path, include

urlpatterns = [
    path("", include("studio.urls")),
    path("login/", include("login.urls")),
    path("mixer/", include("mixer.urls")),
    path("profile/", include("profile.urls")),
]
