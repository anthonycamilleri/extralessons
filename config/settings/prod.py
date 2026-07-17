"""Production settings: real SMTP, WhatsApp Cloud API when enabled."""
from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

NOTIFICATION_CHANNELS = {
    "EMAIL": "apps.notifications.channels.email.EmailAdapter",
    "WHATSAPP": (
        "apps.notifications.channels.whatsapp.WhatsAppCloudAdapter"
        if env.bool("WHATSAPP_ENABLED", default=False)
        else "apps.notifications.channels.whatsapp.StubWhatsAppAdapter"
    ),
}

CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
