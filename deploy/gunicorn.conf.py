"""Gunicorn tuning for a Serverless Container.

The shape is the opposite of the usual VM advice. On a VM you size workers to
the box and let one process handle everything; here the *platform* scales, so
each instance should stay small and predictable. One process with threads keeps
memory flat and — more importantly — keeps the number of database connections
one instance can hold bounded, which matters when the platform is free to run
50 instances against a database whose connection ceiling scales with its
allocated compute.

Set the container's max-concurrency to match `threads`.
"""
import os

from gunicorn import glogging

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"

# One process, threads for concurrency: a Django request here is mostly waiting
# on the database or an HTTP call, not burning CPU.
workers = int(os.environ.get("GUNICORN_WORKERS", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
worker_class = "gthread"

# Import the app in the master before forking: the fork is then nearly free,
# which is most of what a cold start is. Django opens no database connection at
# import time, so nothing shared is a live socket.
preload_app = True

# Must stay below the container's configured request timeout, so gunicorn is the
# one that gives up first and returns a real response.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

_health_path = os.environ.get("HEALTH_CHECK_PATH", "/_health")


class HealthCheckFilteringLogger(glogging.Logger):
    """Keep the platform's probe out of the access log.

    The probe runs continuously against every instance and says nothing. Logs
    are billed by volume on Cockpit, so this is a real line item, and it keeps
    the log readable when something is actually wrong.
    """

    def access(self, resp, req, environ, request_time):
        if environ.get("PATH_INFO") == _health_path:
            return
        super().access(resp, req, environ, request_time)


logger_class = HealthCheckFilteringLogger
