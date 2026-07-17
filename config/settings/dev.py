"""Development settings: console email, stub WhatsApp, DEBUG on."""
from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

NOTIFICATION_CHANNELS = {
    "EMAIL": "apps.notifications.channels.email.EmailAdapter",
    "WHATSAPP": "apps.notifications.channels.whatsapp.StubWhatsAppAdapter",
}
