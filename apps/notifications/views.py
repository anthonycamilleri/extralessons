"""Pictures for rich-text announcements.

The editor (static/js/richtext.js) posts each picture here as it is inserted
and gets back the URL to embed. Anyone who can compose an announcement may
upload: school admins for the admin composer, providers for theirs. The file
goes through the same optimisation as a class cover image and into the
default storage — the database in production, S3 where configured, disk in
development — under a name that is never reused, so an email sent today still
shows its picture in a year.

Inline (cid:) attachments are deliberately not used: the ZeptoMail backend
cannot send them, and a hosted picture is one copy for 150 families rather
than 150 copies.
"""
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.conf import settings
from django.http import JsonResponse
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from PIL import Image, UnidentifiedImageError

from apps.accounts.admin_permissions import is_school_admin
from apps.accounts.models import User
from apps.catalog.images import optimize_image

# Plenty for a 600px email column on a high-density screen.
EMAIL_IMAGE_MAX_DIMENSION = 1200
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def may_compose_announcements(request):
    user = request.user
    return user.is_superuser or is_school_admin(request) or user.role == User.Role.PROVIDER


def _absolute_media_url(name):
    url = default_storage.url(name)
    if url.startswith(("http://", "https://")):
        return url  # S3 already answers with a full URL
    return f"{settings.SITE_URL}{url}"


@login_required
@require_POST
def upload_announcement_image(request):
    if not may_compose_announcements(request):
        raise PermissionDenied
    uploaded = request.FILES.get("image")
    if uploaded is None:
        return JsonResponse({"error": "Choose a picture to upload."}, status=400)
    if uploaded.size > MAX_UPLOAD_BYTES:
        return JsonResponse(
            {"error": f"That picture is too big (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)."},
            status=400,
        )
    if not (uploaded.content_type or "").startswith("image/"):
        return JsonResponse({"error": "Only pictures can be added to a message."}, status=400)
    try:
        # Decoding with Pillow is the real check that this is an image.
        content = optimize_image(uploaded, max_dimension=EMAIL_IMAGE_MAX_DIMENSION)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return JsonResponse({"error": "That file is not a picture we can read."}, status=400)
    stem = slugify(Path(uploaded.name).stem)[:40] or "image"
    name = default_storage.save(f"announcements/{stem}.jpg", content)
    return JsonResponse({"url": _absolute_media_url(name)})
