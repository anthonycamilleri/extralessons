"""Production settings.

Written for a managed container platform (Render today, Scaleway Serverless
before it) and deliberately neutral between them: the platform is described by
environment variables, not by code. The same module serves all three roles —
web, migrate, notifier — because they are the same image with a different
start command. Everything that differs between them is an argument, not a
setting.

The database section is shaped by managed PostgreSQL offerings that front or
cap connections; see docs/render-setup.md for the reasoning.
"""
from .base import *  # noqa: F401,F403
from .base import ALLOWED_HOSTS, DATABASES, MEDIA_MAX_AGE, STORAGES, env, is_postgres

DEBUG = False

# --- Hosts ---
# Django 400s any Host it was not told about, so the platform's generated
# hostname has to be listed alongside any custom domain. Render passes its
# generated hostname in as RENDER_EXTERNAL_HOSTNAME; picking it up here means a
# fresh service answers on *.onrender.com before anyone has configured DNS,
# and keeps answering there after they have.
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
_platform_host = env("RENDER_EXTERNAL_HOSTNAME", default="")
if _platform_host:
    if _platform_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_platform_host)
    if f"https://{_platform_host}" not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(f"https://{_platform_host}")
    # Notification links need an absolute base URL. The generated hostname is
    # the right default until SITE_URL is set to the custom domain.
    if not env("SITE_URL", default=""):
        SITE_URL = f"https://{_platform_host}"

# --- Database ---
if is_postgres():
    # Django's own psycopg pool is the right client-side shape for a managed
    # database whose connection ceiling is a plan limit:
    #   * it caps how many connections one instance can hold, which matters
    #     when the platform is free to run many of them;
    #   * with a pool configured, Django applies session setup (timezone, role)
    #     through the pool's connect hook instead of per-checkout, so it does
    #     not depend on session state a server-side pooler may recycle.
    # Django refuses to combine pooling with persistent connections, so
    # CONN_MAX_AGE stays 0 whenever the pool is on.
    if env.bool("DB_POOL", default=True):
        DATABASES["default"]["CONN_MAX_AGE"] = 0
        DATABASES["default"].setdefault("OPTIONS", {})
        DATABASES["default"]["OPTIONS"]["pool"] = {
            "min_size": env.int("DB_POOL_MIN_SIZE", default=1),
            "max_size": env.int("DB_POOL_MAX_SIZE", default=4),
            "timeout": env.int("DB_POOL_TIMEOUT", default=10),
        }
    else:
        DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=0)

    # Named server-side cursors (QuerySet.iterator()) outlive a single
    # statement, which a transaction-mode pooler in front of the database does
    # not guarantee. Fetch client-side instead; harmless where there is none.
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

    # Encrypt the connection unless told otherwise, so a DATABASE_URL that
    # forgot ?sslmode=require still connects securely rather than in the
    # clear. DB_SSLMODE=prefer is the escape hatch for a private network
    # whose database does not offer TLS.
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"].setdefault("sslmode", env("DB_SSLMODE", default="require"))

# --- Media files ---
# Container filesystems are ephemeral and per-instance: an upload written by one
# instance does not exist for the next request. By default uploads therefore go
# into the database (apps.media): no second provider, no disk pinning the
# service to one instance, backed up with everything else, and the only uploads
# are class images already shrunk to a small JPEG. Set S3_BUCKET to use an
# S3-compatible bucket instead (S3_ENDPOINT_URL and S3_REGION for your provider;
# the defaults are Scaleway's).
S3_BUCKET = env("S3_BUCKET", default="")
if not S3_BUCKET:
    STORAGES["default"] = {"BACKEND": "apps.media.storage.DatabaseStorage"}
else:
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": S3_BUCKET,
            "endpoint_url": env("S3_ENDPOINT_URL", default="https://s3.fr-par.scw.cloud"),
            "region_name": env("S3_REGION", default="fr-par"),
            "access_key": env("S3_ACCESS_KEY_ID", default=""),
            "secret_key": env("S3_SECRET_ACCESS_KEY", default=""),
            "addressing_style": "virtual",
            "location": env("S3_LOCATION", default="media"),
            # Objects are served straight from the bucket (or a CDN in front of
            # it), so URLs must not carry an expiring signature.
            "querystring_auth": False,
            "default_acl": "public-read",
            # file_overwrite=False makes stored names unique, so a stored object
            # is never replaced under the same URL and can be cached forever.
            "file_overwrite": False,
            "object_parameters": {
                "CacheControl": "public, max-age=%d, immutable" % MEDIA_MAX_AGE,
            },
            # Set to a CDN hostname in front of the bucket to serve media
            # through it.
            "custom_domain": env("S3_CUSTOM_DOMAIN", default=None),
        },
    }

# --- Email ---
# ZeptoMail's API whenever its token is present, plain SMTP otherwise (any
# EMAIL_HOST, including ZeptoMail's own relay). EMAIL_BACKEND set explicitly
# wins over both.
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default=(
        "apps.notifications.backends.zeptomail.ZeptoMailBackend"
        if env("ZEPTOMAIL_SEND_MAIL_TOKEN", default="")
        else "django.core.mail.backends.smtp.EmailBackend"
    ),
)

NOTIFICATION_CHANNELS = {
    "EMAIL": "apps.notifications.channels.email.EmailAdapter",
    "WHATSAPP": (
        "apps.notifications.channels.whatsapp.WhatsAppCloudAdapter"
        if env.bool("WHATSAPP_ENABLED", default=False)
        else "apps.notifications.channels.whatsapp.StubWhatsAppAdapter"
    ),
}

# --- Security ---
# The platform terminates TLS and forwards the original scheme in this header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Safe to leave on: the health probe is answered by config.health before
# SecurityMiddleware runs. Turn it off only if the platform is configured to
# forward plain HTTP without the header above, which would loop.
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 30)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
