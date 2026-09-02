"""Liveness endpoint for the platform's health probe.

Deliberately *not* a Django view. The platform probes the container with whatever
Host header it likes, and probes must keep answering while the database is
cold-starting, so this has to run before ALLOWED_HOSTS validation
(`CommonMiddleware`), before `SecurityMiddleware`'s HTTPS redirect, and without
touching the database. Hence a middleware pinned to the top of the stack rather
than an entry in urls.py.

A probe that queried the database would take instances down during a database
cold start — the one moment they are most needed.
"""
from django.conf import settings
from django.http import HttpResponse


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.path = getattr(settings, "HEALTH_CHECK_PATH", "/_health")

    def __call__(self, request):
        if request.path == self.path:
            return HttpResponse(
                b"ok",
                content_type="text/plain",
                headers={"Cache-Control": "no-store"},
            )
        return self.get_response(request)
