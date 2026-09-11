import os
import socket
import logging
import platform
from pathlib import Path
from dotenv import load_dotenv
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.templatetags.static import static
import dj_database_url


def get_local_ip():
    """Get the local IP address of the machine."""
    try:
        # Create a socket and connect to an external address to determine local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "127.0.0.1"


LOCAL_IP = get_local_ip()

os.environ["DJANGO_RUNSERVER_HIDE_WARNING"] = "true"
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR.parent.parent / "env" / ".env")

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get("SECRET_KEY", default="django-insecure-secret-key")

# SECURITY WARNING: don't run with debug turned on in production!
# DEBUG is driven by an explicit env var so the app behaves correctly on any host
# (Render set DEBUG=false; on a plain Droplet there is no RENDER var, so without this
# DEBUG would silently default to True and django-vite would switch to dev-server mode).
DEBUG = os.environ.get("DEBUG", "False").strip().lower() in ("1", "true", "yes", "on")

ALLOWED_HOSTS = []

if DEBUG:
    ALLOWED_HOSTS.extend(
        [
            "localhost",
            ".localhost",  # wildcard: admin.localhost etc. for testing subdomain routing
            "127.0.0.1",
            "0.0.0.0",
            "localhost:5173",
            "127.0.0.1:5173",
            "0.0.0.0:5173",
            LOCAL_IP,
            f"{LOCAL_IP}:5173",
        ]
    )
else:
    # Production hosts from env. A leading-dot entry like ".cosound.ca" is a
    # wildcard: Django's ALLOWED_HOSTS matches the apex domain and every
    # subdomain, so a single value covers all present and future subdomains.
    prod_hosts = os.environ.get("PROD_HOSTS", "")
    if prod_hosts:
        ALLOWED_HOSTS.extend(h.strip() for h in prod_hosts.split(",") if h.strip())

# CSRF Configuration for production
CSRF_TRUSTED_ORIGINS = []

# Add production hosts to CSRF trusted origins (with scheme). A leading-dot
# wildcard like ".cosound.ca" expands to BOTH the subdomain wildcard origin
# and the apex, because Django's CSRF wildcard matches subdomains only.
_prod_hosts_env = os.environ.get("PROD_HOSTS", "")
if _prod_hosts_env:
    for entry in _prod_hosts_env.split(","):
        entry = entry.strip().rstrip("/")
        if not entry:
            continue
        if entry.startswith("."):
            base = entry.lstrip(".")
            CSRF_TRUSTED_ORIGINS.append(f"https://*.{base}")
            CSRF_TRUSTED_ORIGINS.append(f"https://{base}")
            continue
        if "://" not in entry:
            entry = f"https://{entry}"
        CSRF_TRUSTED_ORIGINS.append(entry)

# Behind a TLS-terminating reverse proxy (e.g. Caddy) in production: trust the
# forwarded scheme so HTTPS detection, CSRF, and secure cookies work correctly.
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "formatters": {
        "simple": {
            # 1. Add %(asctime)s to the format string
            "format": "[%(asctime)s] %(message)s",
            # 2. (Optional) Define how the date looks
            # This mimics the default Django look (03/Jan/2026 08:24:22)
            "datefmt": "%d/%b/%Y %H:%M:%S",
        },
        "no_timestamp": {
            "format": "%(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
        "console_no_timestamp": {
            "class": "logging.StreamHandler",
            "formatter": "no_timestamp",
        },
    },
    "loggers": {
        "django.tasks": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "background_task": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "core.tasks": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.server": {
            "handlers": ["console_no_timestamp"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
INSTALLED_APPS = [
    "import_export",
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",
    "unfold.contrib.import_export",
    "unfold.contrib.guardian",
    "unfold.contrib.simple_history",
    "unfold.contrib.constance",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django_tasks",
    "django_tasks.backends.database",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_vite",
    "django_htmx",
    "django_cotton",
    "widget_tweaks",
    "django_file_form",
    "taggit",
    "storages",
    "anymail",
    "core",
    "app",
    "explore",
    "vote",
    "library",
    "login",
    "studio",
    "profile",
    "allauth",
    "allauth.account",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # Host-based URLconf switch (admin/api subdomains -> app at root). Must
    # precede CommonMiddleware so request.urlconf is set before URL resolution.
    "config.middleware.SubdomainURLConf",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": False,
        "OPTIONS": {
            "loaders": [
                [
                    "django.template.loaders.cached.Loader",
                    [
                        "django_cotton.cotton_loader.Loader",
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                ]
            ],
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
            "builtins": [
                "django_cotton.templatetags.cotton",
            ],
        },
    },
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# All web and prediction-worker processes share this notification transport.
# Redis holds no authoritative player state; clients reconcile through the API.
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [{
                "address": REDIS_URL,
                "socket_connect_timeout": 2,
                # Channels blocks for five seconds waiting for notifications.
                # redis-py's default read timeout can expire first and close
                # healthy idle sockets. Publish/subscribe operations retain
                # their application-level timeouts.
                "socket_timeout": None,
            }],
            "expiry": 60,
            "group_expiry": 120,
        },
    },
}
PLAYER_API_RATE = os.environ.get("PLAYER_API_RATE", "120/m")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = os.getenv("AWS_STORAGE_BUCKET_NAME")
AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME")
AWS_S3_SIGNATURE_VERSION = "s3v4"

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": AWS_ACCESS_KEY_ID,
            "secret_key": AWS_SECRET_ACCESS_KEY,
            "bucket_name": AWS_STORAGE_BUCKET_NAME,
            "region_name": AWS_S3_REGION_NAME,
            "endpoint_url": (
                f"https://s3.{AWS_S3_REGION_NAME}.amazonaws.com"
                if AWS_S3_REGION_NAME
                else None
            ),
            "signature_version": AWS_S3_SIGNATURE_VERSION,
        },
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Production static files configuration
if not DEBUG:
    STORAGES["staticfiles"] = {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    }

# django-file-form configuration
# S3 direct uploads are enabled by passing s3_upload_dir to the form constructor
# The AWS_* settings are already configured above for django-storages
FILE_FORM_UPLOAD_DIR = "file-form-uploads"
FILE_FORM_MUST_LOGIN = True  # Require authentication for uploads

# Upload size limit - must be >= chunk size (default 2.5MB)
# See: https://mbraak.github.io/django-file-form/details/#production
DATA_UPLOAD_MAX_MEMORY_SIZE = 3 * 1024 * 1024  # 3MB (slightly above 2.5MB chunk size)

# Tell Django to copy static assets into a path called `staticfiles` (for Render)
STATIC_ROOT = BASE_DIR / "staticfiles"
STATIC_URL = "/static/"
MEDIA_URL = f"https://{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com/"

STATICFILES_DIRS = [
    BASE_DIR / "vite/static",
]
DJANGO_VITE = {
    "default": {
        "dev_mode": DEBUG,
        "dev_server_host": LOCAL_IP,
        "dev_server_port": 5173,
        "manifest_path": BASE_DIR / "vite/static/manifest.json",
    }
}
AUTH_PASSWORD_VALIDATORS = []
AUTH_USER_MODEL = "core.User"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "username*"]
ACCOUNT_EMAIL_VERIFICATION = "optional"
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_ADAPTER = "login.adapters.UnifiedLoginAdapter"
ACCOUNT_FORMS = {
    "request_login_code": "login.adapters.UnifiedRequestLoginCodeForm",
}
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

ANYMAIL = {
    "AMAZON_SES_CLIENT_PARAMS": {
        "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID"),
        "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
        "region_name": "us-east-1",
    },
}

EMAIL_BACKEND = "anymail.backends.amazon_ses.EmailBackend"
DEFAULT_FROM_EMAIL = "auth@cosound.ca"

# Database configuration
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases
DATABASES = {
    "default": dj_database_url.config(
        # For local development, you can use SQLite or a local PostgreSQL
        default="sqlite:///" + str(BASE_DIR / "db.sqlite3"),
        conn_max_age=600,
    )
}

# Cache configuration
# Use database cache for production (works with multiple gunicorn workers)
# Local-memory cache doesn't work with multi-process servers like gunicorn
# See: https://github.com/mbraak/django-file-form/issues/574
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}

TASKS = {
    "default": {
        "BACKEND": "django_tasks.backends.database.DatabaseBackend",
    }
}

IMPORT_EXPORT_TMP_STORAGE_CLASS = "import_export.tmp_storages.CacheStorage"

# Vote app
# Seconds a listener must wait between consecutive votes.
VOTE_THROTTLE_SECONDS = int(os.environ.get("VOTE_THROTTLE_SECONDS", 60))

UNFOLD = {
    "SITE_TITLE": "Management Panel",
    "SITE_HEADER": "Management Console",
    "SITE_ICON": {
        "light": lambda request: static("core/img/logo.png"),  # light mode
        "dark": lambda request: static("core/img/logo.png"),  # dark mode
    },
    "SITE_FAVICONS": [
        {
            "rel": "icon",
            "sizes": "46x46",
            "type": "image/png",
            "href": lambda request: static("core/img/icon.png"),
        },
    ],
    "SIDEBAR": {
        "show_search": True,  # Search in applications and models names
        "command_search": True,  # Replace the sidebar search with the command search
        "show_all_applications": True,  # Dropdown with all applications and models
        "navigation": [
            {
                "title": _("Core Navigation"),
                "separator": True,  # Top border
                "items": [
                    {
                        "title": _("Managers"),
                        "icon": "shield_person",
                        "link": reverse_lazy("admin:core_manager_changelist"),
                    },
                    {
                        "title": _("Players"),
                        "icon": "radio",
                        "link": reverse_lazy("admin:core_player_changelist"),
                    },
                    {
                        "title": _("Posts"),
                        "icon": "article",
                        "link": reverse_lazy("admin:core_post_changelist"),
                    },
                    {
                        "title": _("Local posts"),
                        "icon": "description",
                        "link": reverse_lazy("admin:core_localpost_changelist"),
                    },
                    {
                        "title": _("Sounds"),
                        "icon": "library_music",
                        "link": reverse_lazy("admin:core_sound_changelist"),
                    },
                    {
                        "title": _("Listeners"),
                        "icon": "ear_sound",
                        "link": reverse_lazy("admin:core_listener_changelist"),
                    },
                ],
            },
        ],
    },
}

# COSOUND_CORE_PREDICTOR = "app.predict.predictor_v1"
