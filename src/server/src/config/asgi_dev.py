"""ASGI development entry point with Django's static-file discovery."""

from django.conf import settings
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

from config.asgi import application as asgi_application


application = (
    ASGIStaticFilesHandler(asgi_application) if settings.DEBUG else asgi_application
)
