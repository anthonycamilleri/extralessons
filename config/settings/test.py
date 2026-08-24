"""Test settings.

Runs on whatever DATABASE_URL points at — SQLite for a fast local loop,
PostgreSQL for the row-locking tests. CI runs both.

Note that the suite cannot target Serverless SQL Database: creating the test
database needs `CREATE DATABASE`, and Scaleway blocks DDL on databases and
users. Always test against a plain PostgreSQL server.
"""
from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False
ALLOWED_HOSTS = ["*"]

DATABASES = {
    "default": env.db(
        "TEST_DATABASE_URL",
        default=env("DATABASE_URL", default="sqlite://:memory:"),
    ),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False

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

LOGGING["root"]["level"] = env("LOG_LEVEL", default="WARNING").upper()  # noqa: F405
