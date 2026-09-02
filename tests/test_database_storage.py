"""Uploads kept in the database: storage contract, serving view, pruning."""
import io
from datetime import timedelta

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage, storages
from django.core.management import call_command
from django.utils import timezone
from PIL import Image

from apps.media.models import StoredFile
from apps.media.storage import DatabaseStorage

from .factories import ActivityClassFactory

pytestmark = pytest.mark.django_db



@pytest.fixture(autouse=True)
def database_storage(settings):
    """Run this module under the production storage, whatever the test settings say."""
    settings.STORAGES = {
        "default": {"BACKEND": "apps.media.storage.DatabaseStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


def jpeg(width=400, height=300):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


class TestStorageContract:
    def test_save_open_size_exists_delete(self):
        storage = DatabaseStorage()
        name = storage.save("classes/photo.jpg", ContentFile(b"abc", name="photo.jpg"))

        assert name.startswith("classes/photo_") and name.endswith(".jpg")
        assert storage.exists(name)
        assert storage.size(name) == 3
        with storage.open(name) as handle:
            assert handle.read() == b"abc"
        row = StoredFile.objects.get(name=name)
        assert row.content_type == "image/jpeg"
        assert row.size == 3

        storage.delete(name)
        assert not storage.exists(name)
        with pytest.raises(FileNotFoundError):
            storage.open(name)

    def test_names_are_never_reused(self):
        storage = DatabaseStorage()
        first = storage.save("classes/photo.jpg", ContentFile(b"1"))
        storage.delete(first)
        second = storage.save("classes/photo.jpg", ContentFile(b"2"))
        assert first != second

    def test_available_name_respects_max_length(self):
        storage = DatabaseStorage()
        name = storage.get_available_name("classes/" + "x" * 300 + ".jpg", max_length=60)
        assert len(name) <= 60
        assert name.startswith("classes/") and name.endswith(".jpg")

    def test_url_points_at_the_serving_view(self):
        assert DatabaseStorage().url("classes/a.jpg") == "/media/classes/a.jpg"

    def test_default_storage_is_the_database_one_under_these_settings(self):
        assert isinstance(storages["default"], DatabaseStorage)
        assert isinstance(default_storage.save("x.txt", ContentFile(b"1")), str)
        assert StoredFile.objects.count() == 1

    def test_listdir(self):
        storage = DatabaseStorage()
        storage.save("classes/a.jpg", ContentFile(b"1"))
        storage.save("classes/sub/b.jpg", ContentFile(b"2"))
        storage.save("other.txt", ContentFile(b"3"))
        directories, files = storage.listdir("classes")
        assert directories == ["sub"]
        assert len(files) == 1 and files[0].startswith("a_")
        directories, files = storage.listdir("")
        assert directories == ["classes"]
        assert len(files) == 1 and files[0].startswith("other_")


class TestServing:
    def test_class_image_round_trips_through_the_view(self, client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        cls = ActivityClassFactory(
            image=SimpleUploadedFile("cover.jpg", jpeg(2000, 1000), content_type="image/jpeg")
        )
        cls.refresh_from_db()
        assert cls.image.url.startswith("/media/classes/cover_")

        response = client.get(cls.image.url)
        assert response.status_code == 200
        assert response["Content-Type"] == "image/jpeg"
        assert response["Cache-Control"] == "public, max-age=31536000, immutable"
        assert "Content-Disposition" not in response
        assert int(response["Content-Length"]) == len(response.content)
        with Image.open(io.BytesIO(response.content)) as served:
            assert max(served.size) <= 1600

    def test_unknown_name_is_404(self, client):
        assert client.get("/media/classes/nope.jpg").status_code == 404

    def test_non_image_content_is_offered_as_a_download(self, client):
        name = DatabaseStorage().save("docs/notes.html", ContentFile(b"<script>1</script>"))
        response = client.get(f"/media/{name}")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/html"
        assert response["Content-Disposition"] == "attachment"

    def test_only_safe_methods(self, client):
        name = DatabaseStorage().save("classes/a.jpg", ContentFile(b"1"))
        assert client.post(f"/media/{name}").status_code == 405


class TestPrune:
    def _age(self, name, hours):
        StoredFile.objects.filter(name=name).update(created_at=timezone.now() - timedelta(hours=hours))

    def test_removes_orphans_keeps_referenced_and_recent(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        cls = ActivityClassFactory(
            image=SimpleUploadedFile("cover.jpg", jpeg(), content_type="image/jpeg")
        )
        cls.refresh_from_db()
        referenced = cls.image.name
        old_orphan = DatabaseStorage().save("classes/old.jpg", ContentFile(b"1"))
        fresh_orphan = DatabaseStorage().save("classes/fresh.jpg", ContentFile(b"2"))
        self._age(referenced, 48)
        self._age(old_orphan, 48)

        out = io.StringIO()
        call_command("prune_stored_files", "--dry-run", stdout=out)
        assert StoredFile.objects.count() == 3
        assert old_orphan in out.getvalue() and "not deleted" in out.getvalue()

        call_command("prune_stored_files", stdout=io.StringIO())
        names = set(StoredFile.objects.values_list("name", flat=True))
        assert names == {referenced, fresh_orphan}

    def test_min_age_zero_removes_fresh_orphans(self):
        DatabaseStorage().save("classes/fresh.jpg", ContentFile(b"2"))
        call_command("prune_stored_files", "--min-age-hours", "0", stdout=io.StringIO())
        assert StoredFile.objects.count() == 0
