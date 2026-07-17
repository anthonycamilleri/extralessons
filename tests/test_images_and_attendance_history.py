import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.catalog.models import generate_sessions
from apps.enrollments import services
from apps.enrollments.models import Attendance

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    ChildFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def upload(width, height, format="PNG", name="photo.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 40, 40)).save(buffer, format=format)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{format.lower()}")


class TestImageOptimization:
    def test_large_upload_is_resized_and_reencoded_as_jpeg(self):
        cls = ActivityClassFactory(image=upload(3000, 2000))
        cls.refresh_from_db()
        assert cls.image.name.endswith(".jpg")
        with Image.open(cls.image.open()) as stored:
            assert max(stored.size) <= 1600
            assert stored.format == "JPEG"

    def test_small_image_keeps_dimensions(self):
        cls = ActivityClassFactory(image=upload(400, 300))
        with Image.open(cls.image.open()) as stored:
            assert stored.size == (400, 300)

    def test_transparency_is_flattened(self):
        buffer = io.BytesIO()
        Image.new("RGBA", (500, 500), (0, 0, 0, 0)).save(buffer, format="PNG")
        cls = ActivityClassFactory(
            image=SimpleUploadedFile("logo.png", buffer.getvalue(), content_type="image/png")
        )
        with Image.open(cls.image.open()) as stored:
            assert stored.mode == "RGB"

    def test_resave_without_new_upload_leaves_image_alone(self):
        cls = ActivityClassFactory(image=upload(800, 600))
        stored_name = cls.image.name
        cls.title = "Renamed"
        cls.save()
        cls.refresh_from_db()
        assert cls.image.name == stored_name


class TestParentAttendanceHistory:
    def _enrolled_child_with_marks(self):
        admin = AdminFactory()
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        cls = ActivityClassFactory()
        enrollment = services.approve_request(services.register(child, cls), admin)
        generate_sessions(cls)
        # mark: first present, second absent (created directly, as a provider would)
        sessions = list(cls.sessions.all()[:2])
        Attendance.objects.create(session=sessions[0], child=child, present=True)
        Attendance.objects.create(session=sessions[1], child=child, present=False)
        return parent, child, cls, enrollment

    def test_parent_sees_marks_and_summary(self, client):
        parent, child, cls, enrollment = self._enrolled_child_with_marks()
        client.force_login(parent)

        response = client.get(reverse("enrollment_attendance", args=[enrollment.pk]))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Present" in content
        assert "Absent" in content
        assert "Attended 1 of 2 sessions" in content

    def test_co_guardian_can_also_view(self, client):
        parent, child, cls, enrollment = self._enrolled_child_with_marks()
        co_parent = UserFactory()
        child.guardian_links.create(user=co_parent)
        client.force_login(co_parent)
        response = client.get(reverse("enrollment_attendance", args=[enrollment.pk]))
        assert response.status_code == 200

    def test_stranger_gets_404(self, client):
        parent, child, cls, enrollment = self._enrolled_child_with_marks()
        client.force_login(UserFactory())
        response = client.get(reverse("enrollment_attendance", args=[enrollment.pk]))
        assert response.status_code == 404
