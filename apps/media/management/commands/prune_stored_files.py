"""Delete stored files that no model field refers to any more.

Replacing a class image saves a new row and leaves the old one behind, on
purpose: the old URL may still be cached or open in a tab, and a storage that
deletes eagerly is one that loses data on a rolled-back transaction. Run this
now and then (the web service's Shell tab, or a cron job) to reclaim the space.

  manage.py prune_stored_files            # delete orphans older than a day
  manage.py prune_stored_files --dry-run  # only report them
  manage.py prune_stored_files --min-age-hours 0
"""
from datetime import timedelta

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db.models import FileField
from django.utils import timezone

from apps.media.models import StoredFile
from apps.media.storage import DatabaseStorage


def referenced_names():
    """Every value held by a FileField backed by DatabaseStorage."""
    names = set()
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if not isinstance(field, FileField):
                continue
            if not isinstance(field.storage, DatabaseStorage):
                continue
            values = (
                model._default_manager.exclude(**{field.attname: ""})
                .exclude(**{f"{field.attname}__isnull": True})
                .values_list(field.attname, flat=True)
            )
            names.update(values)
    return names


class Command(BaseCommand):
    help = "Delete database-stored files that no FileField references."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report, do not delete.")
        parser.add_argument(
            "--min-age-hours",
            type=float,
            default=24,
            help="Leave rows younger than this alone; a save in progress has a row "
            "before its FileField does (default: 24).",
        )

    def handle(self, *args, dry_run, min_age_hours, **options):
        cutoff = timezone.now() - timedelta(hours=min_age_hours)
        orphans = StoredFile.objects.filter(created_at__lt=cutoff).exclude(
            name__in=referenced_names()
        )
        count = orphans.count()
        total = sum(orphans.values_list("size", flat=True))
        for name in orphans.values_list("name", flat=True):
            self.stdout.write(f"  {name}")
        if dry_run:
            self.stdout.write(f"{count} orphaned file(s), {total} bytes — not deleted (dry run).")
            return
        orphans.delete()
        self.stdout.write(f"Deleted {count} orphaned file(s), {total} bytes.")
