"""Development settings: console email, stub WhatsApp, DEBUG on.

Works two ways, both supported:

  * SQLite (default) — `python manage.py runserver`, no Docker, no services.
  * PostgreSQL — set DATABASE_URL, or use `docker compose up`. Required for the
    row-locking behaviour the capacity mutex relies on, so run the test suite
    here before trusting a change to apps/enrollments/services.py.
"""
from .base import *  # noqa: F401,F403
from .base import DATABASES, env, is_postgres

DEBUG = True
ALLOWED_HOSTS = ["*"]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

NOTIFICATION_CHANNELS = {
    "EMAIL": "apps.notifications.channels.email.EmailAdapter",
    "WHATSAPP": "apps.notifications.channels.whatsapp.StubWhatsAppAdapter",
}

if not is_postgres():
    # WAL keeps the dev server responsive while a long request holds a write,
    # and the timeout stops "database is locked" from surfacing as a 500 the
    # moment the notifier and the web process overlap.
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"].update(
        {
            "timeout": env.int("SQLITE_TIMEOUT", default=20),
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        }
    )
