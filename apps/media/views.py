"""Serve database-stored files at MEDIA_URL.

One row read per uncached request. Responses are marked immutable for a year:
names are never reused (see DatabaseStorage.get_available_name), so a browser
or a CDN in front can hold on to them indefinitely.
"""
from django.conf import settings
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_safe

from .models import StoredFile


@require_safe
def serve_stored_file(request, name):
    try:
        row = StoredFile.objects.get(name=name)
    except StoredFile.DoesNotExist:
        raise Http404(name)
    content_type = row.content_type or "application/octet-stream"
    response = HttpResponse(bytes(row.content), content_type=content_type)
    response["Content-Length"] = row.size
    response["Cache-Control"] = f"public, max-age={settings.MEDIA_MAX_AGE}, immutable"
    # Only images are ever meant to render inline. Anything else that found its
    # way in is offered as a download, never interpreted by the browser.
    if not content_type.startswith("image/"):
        response["Content-Disposition"] = "attachment"
    return response
