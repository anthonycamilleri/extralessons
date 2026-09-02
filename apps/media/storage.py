"""A Django Storage backend that keeps files in the database.

Selected as the default storage in production (config/settings/prod.py) unless
an S3 bucket is configured. Files are served by `apps.media.views`, at
MEDIA_URL, with immutable caching — which is safe because names are never
reused: every save gets a fresh random suffix, so a replaced image is a new
URL, not a stale copy in someone's browser cache.

Replacing a class image leaves the previous row behind (exactly as an S3
bucket without overwrite would). `manage.py prune_stored_files` removes rows
no FileField points at any more.
"""
import mimetypes
import os

from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.utils.deconstruct import deconstructible

from .models import StoredFile

SUFFIX_LENGTH = 8


@deconstructible
class DatabaseStorage(Storage):
    def _open(self, name, mode="rb"):
        if "w" in mode or "a" in mode or "+" in mode:
            raise ValueError("DatabaseStorage files are read-only once saved; save a new file instead.")
        try:
            row = StoredFile.objects.only("content").get(name=name)
        except StoredFile.DoesNotExist:
            raise FileNotFoundError(name) from None
        return ContentFile(bytes(row.content), name=name)

    def _save(self, name, content):
        if hasattr(content, "seek"):
            content.seek(0)
        data = content.read()
        content_type = getattr(content, "content_type", None) or mimetypes.guess_type(name)[0] or ""
        StoredFile.objects.create(
            name=name, content=data, content_type=content_type, size=len(data)
        )
        return name

    def get_available_name(self, name, max_length=None):
        """Always mint a new name: "dir/stem_<random>.ext".

        Django's default only appends a suffix on collision, which would let a
        deleted-then-re-uploaded "photo.jpg" reappear at its old URL — and
        that URL is cached as immutable.
        """
        dir_name, file_name = os.path.split(name)
        root, ext = os.path.splitext(file_name)
        while True:
            suffix = f"_{get_random_string(SUFFIX_LENGTH)}"
            if max_length is not None:
                overhead = len(os.path.join(dir_name, suffix + ext))
                root = root[: max(0, max_length - overhead)]
            candidate = os.path.join(dir_name, f"{root}{suffix}{ext}")
            if not self.exists(candidate):
                return candidate

    def exists(self, name):
        return StoredFile.objects.filter(name=name).exists()

    def delete(self, name):
        StoredFile.objects.filter(name=name).delete()

    def size(self, name):
        try:
            return StoredFile.objects.values_list("size", flat=True).get(name=name)
        except StoredFile.DoesNotExist:
            raise FileNotFoundError(name) from None

    def get_created_time(self, name):
        try:
            return StoredFile.objects.values_list("created_at", flat=True).get(name=name)
        except StoredFile.DoesNotExist:
            raise FileNotFoundError(name) from None

    get_modified_time = get_created_time

    def url(self, name):
        if name is None:
            raise ValueError("This file has no name.")
        return reverse("stored_file", kwargs={"name": name})

    def listdir(self, path):
        prefix = path.strip("/")
        prefix = f"{prefix}/" if prefix else ""
        directories, files = set(), []
        for name in StoredFile.objects.filter(name__startswith=prefix).values_list("name", flat=True):
            rest = name[len(prefix):]
            head, sep, _ = rest.partition("/")
            if sep:
                directories.add(head)
            else:
                files.append(head)
        return sorted(directories), files
