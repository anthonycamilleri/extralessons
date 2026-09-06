"""A freeze switch for the whole site, for moving the database between hosts.

Set MAINTENANCE_MODE=true on the running platform and every request except the
health probe gets a 503 with a short explanation and a Retry-After header. No
login, no registration, no admin: nothing can write to the database while it
is being copied, so the copy is complete rather than "complete apart from the
family who registered at 22:47".

Like the health probe it sits at the top of the middleware stack and touches
neither the session nor the database. The page is a standalone template — it
does not extend base.html, whose context processors would query SiteConfig —
so it renders even when the database is already gone.
"""
from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string


class MaintenanceModeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.health_path = getattr(settings, "HEALTH_CHECK_PATH", "/_health")

    def __call__(self, request):
        if not getattr(settings, "MAINTENANCE_MODE", False) or request.path == self.health_path:
            return self.get_response(request)
        body = render_to_string(
            "maintenance.html",
            {"message": getattr(settings, "MAINTENANCE_MESSAGE", "")},
        )
        return HttpResponse(
            body,
            status=503,
            headers={
                "Retry-After": str(getattr(settings, "MAINTENANCE_RETRY_AFTER", 600)),
                "Cache-Control": "no-store",
            },
        )
