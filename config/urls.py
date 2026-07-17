from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("me/", include("apps.dashboards.parent_urls")),
    path("provider/", include("apps.dashboards.provider_urls")),
    path("admin-tools/", include("apps.dashboards.admintools_urls")),
    path("", include("apps.catalog.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
