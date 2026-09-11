import os
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from django.urls import path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_application = get_asgi_application()

# Import consumers only after Django has initialized its model registry.
from app.consumers import PlayerConsumer

application = ProtocolTypeRouter({
    "http": django_application,
    "websocket": URLRouter([
        path("ws/player/", PlayerConsumer.as_asgi()),
    ]),
})
