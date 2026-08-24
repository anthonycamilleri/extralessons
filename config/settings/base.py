"""Base settings shared by all environments.

Everything here is environment-driven so the same image runs unchanged as a
Serverless Container (web), as a Serverless Job (migrations, notifier), and on
a laptop against SQLite. Environment-specific modules layer on top:
`dev` (local), `prod` (Scaleway), `test` (pytest).
"""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# Read .env from the project root when present (local development only —
# on Scaleway the values arrive as container/job environment variables).
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "apps.accounts",
    "apps.catalog",
    "apps.enrollments",
    "apps.notifications",
    "apps.dashboards",
]

MIDDLEWARE = [
    # First on purpose: the platform's health probe must be answered before
    # ALLOWED_HOSTS validation and before any HTTPS redirect. See config.health.
    "config.health.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

HEALTH_CHECK_PATH = env("HEALTH_CHECK_PATH", default="/_health")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.context_processors.site_config",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database ---
# SQLite is the zero-setup default so `python manage.py runserver` works with
# nothing installed but Python. Point DATABASE_URL at Postgres for parity with
# production (and for the row-locking tests, which SQLite cannot express).
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False


def is_postgres(alias="default"):
    """True when the given connection targets PostgreSQL."""
    return "postgresql" in DATABASES[alias]["ENGINE"]


AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "post_login"
LOGOUT_REDIRECT_URL = "catalogue"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", default="Europe/Malta")
USE_I18N = True
USE_TZ = True

# --- Static files ---
# Served by WhiteNoise from inside the container: hashed filenames are baked in
# at build time, so there is no deploy-ordering problem and no extra network hop
# on a cold start. Put a CDN in front for caching, not for origin duty.
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
WHITENOISE_MAX_AGE = env.int("WHITENOISE_MAX_AGE", default=60 * 60 * 24 * 365)

# --- Media files ---
# Overridden to S3-compatible Object Storage in prod: container filesystems are
# ephemeral and per-instance, so uploads must not live on local disk.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MEDIA_URL = "media/"
MEDIA_ROOT = env("MEDIA_ROOT", default=str(BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Email ---
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=15)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@example.com")

# --- Notifications ---
# Channel adapters are configurable per environment; see apps.notifications.channels.
NOTIFICATION_CHANNELS = {
    "EMAIL": "apps.notifications.channels.email.EmailAdapter",
    "WHATSAPP": "apps.notifications.channels.whatsapp.StubWhatsAppAdapter",
}
WHATSAPP_ENABLED = env.bool("WHATSAPP_ENABLED", default=False)
WHATSAPP_ACCESS_TOKEN = env("WHATSAPP_ACCESS_TOKEN", default="")
WHATSAPP_PHONE_NUMBER_ID = env("WHATSAPP_PHONE_NUMBER_ID", default="")
WHATSAPP_API_VERSION = env("WHATSAPP_API_VERSION", default="v20.0")

NOTIFIER_BATCH_SIZE = env.int("NOTIFIER_BATCH_SIZE", default=20)
NOTIFIER_MAX_ATTEMPTS = env.int("NOTIFIER_MAX_ATTEMPTS", default=5)
NOTIFIER_IDLE_SLEEP_SECONDS = env.int("NOTIFIER_IDLE_SLEEP_SECONDS", default=5)
# Time budget for `run_notifier --drain`, the mode a scheduled Serverless Job
# uses. Keep it comfortably under the job timeout.
NOTIFIER_DRAIN_MAX_SECONDS = env.int("NOTIFIER_DRAIN_MAX_SECONDS", default=300)

# Deliver queued notifications inline, right after the state change commits,
# instead of waiting for the scheduled sweep. Safe because the outbox already
# treats a failed send as a first-class state: an inline failure leaves exactly
# the row a failed worker send would have left.
NOTIFIER_INLINE_DELIVERY = env.bool("NOTIFIER_INLINE_DELIVERY", default=True)
# How long an inline pass may spend sending before handing the rest back to the
# queue. This lands in a user's request, so it is a latency budget, not a
# throughput one — the common case (a transition queueing two or three rows)
# finishes in well under a second.
NOTIFIER_INLINE_MAX_SECONDS = env.int("NOTIFIER_INLINE_MAX_SECONDS", default=20)

# Absolute base URL used in notification links (no trailing slash).
SITE_URL = env("SITE_URL", default="http://localhost:8000")

# --- Logging ---
# Serverless has no `docker compose logs`: stdout is the only channel, and it is
# what Scaleway Cockpit collects. Django's default console handler is gated
# behind DEBUG, so without this every logger.info in the notifier disappears in
# production.
LOG_LEVEL = env("LOG_LEVEL", default="INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "plain",
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        # Query logging is deafening and doubles as a PII leak; keep it off.
        "django.db.backends": {"level": "WARNING", "propagate": True},
    },
}
