"""Uploaded files, stored as rows.

Why a table and not a disk or a bucket: the platform (Render) offers no object
storage of its own, and a persistent disk pins the web service to a single
instance and turns every deploy into a few seconds of downtime. The database
is already there, already backed up, and already reachable from every instance
— and the only uploads this application takes are class cover images, which
`apps.catalog.images` shrinks to a JPEG of at most 1600px before they get here.
A hundred classes is a few tens of megabytes. See docs/render-setup.md.
"""
from django.db import models


class StoredFile(models.Model):
    # The storage name (e.g. "classes/chess-club_a1b2c3d4.jpg"): also the tail
    # of the URL it is served on. Always unique, never reused, so served copies
    # can be cached forever.
    name = models.CharField(max_length=255, unique=True)
    content = models.BinaryField(editable=False)
    content_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "stored file"

    def __str__(self):
        return self.name
