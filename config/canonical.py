"""Send the bare domain to the canonical hostname.

Parents type `esljparents.eu`; the site lives at `www.esljparents.eu`. Serving
both would split sessions and cookies between two hostnames and put two
different URLs in circulation. Render's edge redirected the apex for us; on
Scaleway nothing does, so the app does it itself: a request whose Host is one
of CANONICAL_REDIRECT_HOSTS is answered with a permanent redirect to the same
path on SITE_URL. Every other host (the canonical one, the platform's generated
endpoint) is served as usual.

Sits above SecurityMiddleware so a plain-HTTP request to the apex hops straight
to https://www... in one redirect, and above the session layer so it costs no
database work. The health probe is never redirected.
"""
from django.conf import settings
from django.http import HttpResponsePermanentRedirect


class CanonicalHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.health_path = getattr(settings, "HEALTH_CHECK_PATH", "/_health")
        self.redirect_hosts = {
            h.strip().lower() for h in getattr(settings, "CANONICAL_REDIRECT_HOSTS", []) if h.strip()
        }
        self.site_url = getattr(settings, "SITE_URL", "").rstrip("/")

    def __call__(self, request):
        if self.redirect_hosts and self.site_url and request.path != self.health_path:
            # The raw header, not get_host(): validation against ALLOWED_HOSTS
            # is CommonMiddleware's job and happens later for everything else.
            host = request.META.get("HTTP_HOST", "").split(":")[0].lower()
            if host in self.redirect_hosts:
                return HttpResponsePermanentRedirect(self.site_url + request.get_full_path())
        return self.get_response(request)
