"""Test settings: local Postgres, in-memory email, fast hashing."""
from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False
ALLOWED_HOSTS = ["*"]

DATABASES = {
    "default": env.db(
        "TEST_DATABASE_URL",
        default=env("DATABASE_URL", default="postgres://app:app@localhost:5432/extralessons"),
    ),
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

NOTIFICATION_CHANNELS = {
    "EMAIL": "apps.notifications.channels.email.EmailAdapter",
    "WHATSAPP": "apps.notifications.channels.whatsapp.StubWhatsAppAdapter",
}
