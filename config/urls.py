from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.catalog.mcp_http import mcp_endpoint
from apps.media.views import serve_stored_file

urlpatterns = [
    path("admin/", admin.site.urls),
    # Remote MCP for Claude Desktop / Cowork / claude.ai custom connectors.
    # Both spellings, so a URL pasted with a trailing slash is not a 404.
    path("mcp", mcp_endpoint, name="mcp"),
    path("mcp/", mcp_endpoint),
    path("accounts/", include("apps.accounts.urls")),
    path("me/", include("apps.dashboards.parent_urls")),
    path("provider/", include("apps.dashboards.provider_urls")),
    path("admin-tools/", include("apps.dashboards.admintools_urls")),
    path("", include("apps.catalog.urls")),
]

if settings.DEBUG:
    # Development: uploads on local disk, served by Django's static helper.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Production: uploads in the database (apps.media), served at the same MEDIA_URL.
# Listed after the DEBUG helper so that, in development, disk wins.
urlpatterns += [
    path(f"{settings.MEDIA_URL.strip('/')}/<path:name>", serve_stored_file, name="stored_file"),
]
