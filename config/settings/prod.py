"""Production settings for Scaleway Serverless.

The same module serves all three runtimes — the Serverless Container (web), the
migration Job, and the notifier Job — because they are the same image with a
different start command. Everything that differs between them is an argument,
not a setting.

Three things here exist specifically because of Serverless SQL Database's
built-in connection pooler; see docs/scaleway-setup.md for the reasoning.
"""
from .base import *  # noqa: F401,F403
from .base import DATABASES, STORAGES, env, is_postgres

DEBUG = False

# --- Hosts ---
# Must list the generated container endpoint (…functions.fnc.fr-par.scw.cloud)
# as well as any custom domain, otherwise Django 400s the platform's traffic.
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# --- Database ---
if is_postgres():
    # Serverless SQL Database fronts every connection with a pooler. Django's
    # own psycopg pool is the right client-side match for it:
    #   * it caps how many connections one instance can hold, which matters
    #     when the platform is free to run 50 of them;
    #   * with a pool configured, Django applies session setup (timezone, role)
    #     through the pool's connect hook instead of per-checkout, so it does
    #     not depend on session state a transaction-mode pooler may recycle.
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
    # statement, and Scaleway documents that cursor handling is not guaranteed
    # across pooled connections. Fetch client-side instead.
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

    # Serverless SQL Database only accepts TLS. Default it on so a DATABASE_URL
    # that forgot ?sslmode=require still connects rather than failing at boot.
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"].setdefault("sslmode", "require")

# --- Media files on Object Storage ---
# Container filesystems are ephemeral and per-instance: an upload written by one
# instance does not exist for the next request. Without a bucket configured the
# app still boots on local disk, so a first deploy is not blocked on it — but
# uploads will not survive.
S3_BUCKET = env("S3_BUCKET", default="")
if S3_BUCKET:
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
                "CacheControl": "public, max-age=%d, immutable"
                % env.int("MEDIA_MAX_AGE", default=60 * 60 * 24 * 365),
            },
            # Set to the Edge Services domain to serve media through the CDN.
            "custom_domain": env("S3_CUSTOM_DOMAIN", default=None),
        },
    }

# --- Email ---
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

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
