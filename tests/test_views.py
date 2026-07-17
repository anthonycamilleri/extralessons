import pytest
from django.urls import reverse

from apps.enrollments import services
from apps.enrollments.models import Enrollment

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    ChildFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


class TestPublicCatalogue:
    def test_catalogue_lists_published_classes_with_availability(self, client):
        cls = ActivityClassFactory(title="Chess Club", capacity=5)
        ActivityClassFactory(title="Hidden Draft", status="DRAFT")

        response = client.get(reverse("catalogue"))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Chess Club" in content
        assert "5 of 5 places available" in content
        assert "Hidden Draft" not in content

    def test_class_detail_shows_description(self, client):
        cls = ActivityClassFactory(description="Learn openings and tactics.")
        response = client.get(cls.get_absolute_url())
        assert response.status_code == 200
        assert "Learn openings and tactics." in response.content.decode()


class TestSignupAndFamily:
    def test_signup_creates_parent_account(self, client):
        response = client.post(
            reverse("signup"),
            {
                "email": "new@parent.test",
                "first_name": "New",
                "last_name": "Parent",
                "phone_e164": "",
                "password1": "s3cure-pass-123",
                "password2": "s3cure-pass-123",
            },
        )
        assert response.status_code == 302
        assert response.url == reverse("parent_home")

    def test_parent_can_add_child(self, client):
        parent = UserFactory()
        client.force_login(parent)
        response = client.post(
            reverse("child_add"),
            {"first_name": "Ada", "last_name": "Test", "date_of_birth": "2018-04-01", "notes": ""},
        )
        assert response.status_code == 302
        assert parent.children.filter(first_name="Ada").exists()


class TestEnrollmentViews:
    def test_parent_can_request_place(self, client):
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        cls = ActivityClassFactory()
        client.force_login(parent)

        response = client.post(reverse("enroll", args=[cls.pk]), {"child": child.pk})

        assert response.status_code == 302
        assert Enrollment.objects.filter(
            child=child, activity_class=cls, status=Enrollment.Status.REQUESTED
        ).exists()

    def test_parent_cannot_touch_other_familys_enrollment(self, client):
        enrollment = services.register(ChildFactory(), ActivityClassFactory())
        stranger = UserFactory()
        client.force_login(stranger)

        response = client.post(reverse("enrollment_cancel", args=[enrollment.pk]))

        assert response.status_code == 404
        enrollment.refresh_from_db()
        assert enrollment.status == Enrollment.Status.REQUESTED

    def test_parent_cannot_enroll_someone_elses_child(self, client):
        other_child = ChildFactory()
        cls = ActivityClassFactory()
        stranger = UserFactory()
        client.force_login(stranger)

        response = client.post(reverse("enroll", args=[cls.pk]), {"child": other_child.pk})

        assert response.status_code == 404
        assert not Enrollment.objects.filter(child=other_child).exists()

    def test_offer_confirm_via_dashboard(self, client):
        admin = AdminFactory()
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        cls = ActivityClassFactory(capacity=1)
        blocker = services.approve_request(
            services.register(ChildFactory(), cls), admin
        )
        waitlisted = services.approve_request(services.register(child, cls), admin)
        services.cancel(blocker, Enrollment.CancelReason.PARENT)
        offered = services.offer_seat(waitlisted, admin)

        client.force_login(parent)
        response = client.post(reverse("offer_confirm", args=[offered.pk]))

        assert response.status_code == 302
        offered.refresh_from_db()
        assert offered.status == Enrollment.Status.ENROLLED


class TestAdminTools:
    def test_requests_queue_requires_admin(self, client):
        client.force_login(UserFactory())
        assert client.get(reverse("admintools_requests")).status_code == 403

    def test_admin_can_approve_from_queue(self, client):
        admin = AdminFactory()
        enrollment = services.register(ChildFactory(), ActivityClassFactory())
        client.force_login(admin)

        response = client.post(
            reverse("admintools_request_approve", args=[enrollment.pk])
        )

        assert response.status_code == 302
        enrollment.refresh_from_db()
        assert enrollment.status == Enrollment.Status.ENROLLED

    def test_admin_can_offer_seat_from_waitlist_page(self, client):
        admin = AdminFactory()
        cls = ActivityClassFactory(capacity=1)
        enrolled = services.approve_request(services.register(ChildFactory(), cls), admin)
        waitlisted = services.approve_request(services.register(ChildFactory(), cls), admin)
        services.cancel(enrolled, Enrollment.CancelReason.PARENT)
        client.force_login(admin)

        page = client.get(reverse("admintools_waitlist", args=[cls.pk]))
        assert page.status_code == 200

        response = client.post(
            reverse("admintools_waitlist_offer", args=[waitlisted.pk])
        )
        assert response.status_code == 302
        waitlisted.refresh_from_db()
        assert waitlisted.status == Enrollment.Status.OFFERED
