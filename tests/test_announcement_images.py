"""Pictures uploaded from the announcement editor: who may, what is accepted,
where it goes, and that pruning leaves it alone."""
import io
from datetime import timedelta
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from PIL import Image

from apps.media.models import StoredFile
from apps.notifications.models import Broadcast

from .factories import AdminFactory, ProviderUserFactory, SuperAdminFactory, UserFactory

pytestmark = pytest.mark.django_db

URL = reverse("announcement_image_upload")


@pytest.fixture(autouse=True)
def database_storage(settings):
    settings.STORAGES = {
        "default": {"BACKEND": "apps.media.storage.DatabaseStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    settings.SITE_URL = "https://www.example.org"


def picture(width=400, height=300, fmt="PNG", name="Photo Day.png", content_type="image/png"):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=content_type)


class TestWhoMayUpload:
    def test_anonymous_is_sent_to_login(self, client):
        response = client.post(URL, {"image": picture()})
        assert response.status_code == 302
        assert reverse("login") in response.url

    def test_parent_is_forbidden(self, client):
        client.force_login(UserFactory())
        assert client.post(URL, {"image": picture()}).status_code == 403

    @pytest.mark.parametrize("make_user", [AdminFactory, SuperAdminFactory, ProviderUserFactory])
    def test_composers_may_upload(self, client, make_user):
        client.force_login(make_user())
        response = client.post(URL, {"image": picture()})
        assert response.status_code == 200
        url = response.json()["url"]
        assert url.startswith("https://www.example.org/media/announcements/photo-day")
        assert url.endswith(".jpg")

    def test_get_is_not_allowed(self, client):
        client.force_login(SuperAdminFactory())
        assert client.get(URL).status_code == 405


class TestWhatIsAccepted:
    def test_stored_optimised_and_served(self, client):
        client.force_login(SuperAdminFactory())
        response = client.post(URL, {"image": picture(3000, 1500)})
        name = response.json()["url"].split("/media/")[1]

        row = StoredFile.objects.get(name=name)
        assert row.content_type == "image/jpeg"
        image = Image.open(io.BytesIO(bytes(row.content)))
        assert image.size == (1200, 600)  # shrunk for a 600px email column

        served = client.get(reverse("stored_file", kwargs={"name": name}))
        assert served.status_code == 200
        assert served["Content-Type"] == "image/jpeg"

    def test_missing_file(self, client):
        client.force_login(SuperAdminFactory())
        response = client.post(URL, {})
        assert response.status_code == 400
        assert "Choose a picture" in response.json()["error"]

    def test_not_an_image(self, client):
        client.force_login(SuperAdminFactory())
        # Right content type, wrong bytes: only decoding it tells.
        fake = SimpleUploadedFile("x.png", b"definitely not a png", content_type="image/png")
        response = client.post(URL, {"image": fake})
        assert response.status_code == 400
        assert "not a picture" in response.json()["error"]
        assert not StoredFile.objects.exists()

    def test_wrong_content_type(self, client):
        client.force_login(SuperAdminFactory())
        doc = SimpleUploadedFile("notes.pdf", b"%PDF-1.4", content_type="application/pdf")
        response = client.post(URL, {"image": doc})
        assert response.status_code == 400
        assert "Only pictures" in response.json()["error"]

    def test_too_big(self, client):
        client.force_login(SuperAdminFactory())
        with mock.patch("apps.notifications.views.MAX_UPLOAD_BYTES", 1000):
            response = client.post(URL, {"image": picture()})
        assert response.status_code == 400
        assert "too big" in response.json()["error"]

    def test_s3_style_absolute_url_is_passed_through(self, client):
        client.force_login(SuperAdminFactory())
        with mock.patch(
            "apps.notifications.views.default_storage.url",
            return_value="https://cdn.example.org/announcements/photo-day.jpg",
        ):
            response = client.post(URL, {"image": picture()})
        assert response.json()["url"] == "https://cdn.example.org/announcements/photo-day.jpg"


class TestPruning:
    def test_a_picture_in_a_sent_announcement_is_kept(self, client):
        client.force_login(SuperAdminFactory())
        kept_url = client.post(URL, {"image": picture()}).json()["url"]
        client.post(URL, {"image": picture()})  # uploaded, then never used
        Broadcast.objects.create(
            sender=AdminFactory(),
            scope=Broadcast.Scope.ALL_CLASSES,
            subject="Trip",
            body="[Boots]",
            body_html=f'<p><img src="{kept_url}" alt="Boots"></p>',
        )
        StoredFile.objects.update(created_at=StoredFile.objects.first().created_at - timedelta(days=2))
        assert StoredFile.objects.count() == 2

        call_command("prune_stored_files")

        assert StoredFile.objects.count() == 1
        assert kept_url.endswith(StoredFile.objects.get().name)
