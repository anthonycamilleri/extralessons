"""Web app manifest: lets an instructor add the dashboard to their phone's home
screen and open it like an app, full screen, straight to their classes.

Rendered rather than a static file because the name is the school's, which
the office sets in Site configuration. Cached for a day like robots.txt: the
name changes about once, ever.
"""
from django.http import JsonResponse
from django.templatetags.static import static
from django.views.decorators.http import require_GET

from apps.accounts.models import SiteConfig

MAX_AGE = 24 * 60 * 60


@require_GET
def manifest(request):
    school = SiteConfig.get().school_name
    body = {
        "name": f"{school} Activities",
        "short_name": "Activities",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#fbfaf7",
        "theme_color": "#0072bb",
        "icons": [
            {"src": static("img/icon-32.png"), "sizes": "32x32", "type": "image/png"},
            {"src": static("img/icon-180.png"), "sizes": "180x180", "type": "image/png"},
        ],
    }
    return JsonResponse(
        body,
        content_type="application/manifest+json",
        headers={"Cache-Control": f"public, max-age={MAX_AGE}"},
    )
