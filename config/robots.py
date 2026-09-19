"""robots.txt: keep well-behaved crawlers to the public catalogue.

The catalogue and class pages are meant to be found. Everything else is either
behind a login (dashboards, admin), an API (the MCP connector), or a helper
that only makes sense inside a page (announcement uploads, stored images).
Crawling those yields 302s and 401s, and on a serverless host every such
request is a wake-up and a quarter-hour of billed instance for nothing.

No database, no template: the file is a constant, and it is cached hard so a
polite crawler asks once a day rather than on every visit.
"""
from django.http import HttpResponse
from django.views.decorators.http import require_GET

DISALLOWED = (
    "/admin/",
    "/admin-tools/",
    "/accounts/",
    "/me/",
    "/provider/",
    "/mcp",
    "/announcements/",
    "/media/",
    "/_health",
)

MAX_AGE = 24 * 60 * 60

_BODY = "".join(
    ["User-agent: *\n"]
    + [f"Disallow: {path}\n" for path in DISALLOWED]
    + ["Allow: /\n"]
).encode()


@require_GET
def robots_txt(request):
    return HttpResponse(
        _BODY,
        content_type="text/plain; charset=utf-8",
        headers={"Cache-Control": f"public, max-age={MAX_AGE}"},
    )
