import pytest
from django.urls import reverse

from apps.catalog.models import ActivityClass
from apps.enrollments import services
from apps.enrollments.models import Enrollment
from apps.notifications.models import Event, Notification

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    ChildFactory,
    TermFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


class TestAdminBroadcast:
    def test_broadcast_to_all_classes(self, client):
        admin = AdminFactory()
        parent = UserFactory()
        cls = ActivityClassFactory()
        services.register(ChildFactory(parent=parent), cls)
        client.force_login(admin)

        response = client.post(
            reverse("admintools_broadcast"),
            {
                "scope": "ALL_CLASSES",
                "subject": "School closed Friday",
                "body": "Public holiday.",
            },
        )

        assert response.status_code == 302
        assert Notification.objects.filter(
            event=Event.BROADCAST, recipient=parent
        ).exists()

    def test_selected_scope_requires_classes(self, client):
        client.force_login(AdminFactory())
        response = client.post(
            reverse("admintools_broadcast"),
            {"scope": "SELECTED_CLASSES", "subject": "x", "body": "y"},
        )
        assert response.status_code == 200
        assert b"Pick at least one class" in response.content

    def test_broadcast_page_forbidden_for_parents(self, client):
        client.force_login(UserFactory())
        assert client.get(reverse("admintools_broadcast")).status_code == 403


class TestDjangoAdminActions:
    def test_clone_into_term_copies_as_draft(self, admin_client):
        cls = ActivityClassFactory(title="Chess", status="PUBLISHED")
        target = TermFactory(name="Next Term")

        response = admin_client.post(
            reverse("admin:catalog_activityclass_changelist"),
            {
                "action": "clone_into_term",
                "_selected_action": [cls.pk],
                "apply": "1",
                "target_term": target.pk,
            },
        )

        assert response.status_code == 302
        clone = ActivityClass.objects.get(term=target, slug=cls.slug)
        assert clone.status == ActivityClass.Status.DRAFT
        assert clone.title == "Chess"
        assert clone.pk != cls.pk
        assert clone.enrollments.count() == 0

    def test_clone_skips_existing_slug_in_target_term(self, admin_client):
        cls = ActivityClassFactory(slug="chess")
        target = TermFactory()
        ActivityClassFactory(term=target, slug="chess")

        admin_client.post(
            reverse("admin:catalog_activityclass_changelist"),
            {
                "action": "clone_into_term",
                "_selected_action": [cls.pk],
                "apply": "1",
                "target_term": target.pk,
            },
        )
        assert ActivityClass.objects.filter(term=target, slug="chess").count() == 1

    def test_publish_action_generates_sessions(self, admin_client):
        cls = ActivityClassFactory(status="DRAFT")
        admin_client.post(
            reverse("admin:catalog_activityclass_changelist"),
            {"action": "publish_classes", "_selected_action": [cls.pk]},
        )
        cls.refresh_from_db()
        assert cls.status == ActivityClass.Status.PUBLISHED
        assert cls.sessions.count() > 0

    def test_cancel_class_action_notifies_families(self, admin_client):
        admin = AdminFactory()
        cls = ActivityClassFactory()
        enrollment = services.approve_request(
            services.register(ChildFactory(), cls), admin
        )

        admin_client.post(
            reverse("admin:catalog_activityclass_changelist"),
            {"action": "cancel_classes", "_selected_action": [cls.pk]},
        )

        cls.refresh_from_db()
        enrollment.refresh_from_db()
        assert cls.status == ActivityClass.Status.CANCELLED
        assert enrollment.status == Enrollment.Status.CANCELLED
        assert enrollment.cancel_reason == Enrollment.CancelReason.CLASS_CANCELLED
        assert Notification.objects.filter(
            event=Event.CLASS_CANCELLED, enrollment=enrollment
        ).exists()

    def test_capacity_increase_alerts_admins_when_waitlist_exists(self, admin_client):
        admin = AdminFactory()
        cls = ActivityClassFactory(capacity=1)
        services.approve_request(services.register(ChildFactory(), cls), admin)
        services.approve_request(services.register(ChildFactory(), cls), admin)
        assert not Notification.objects.filter(event=Event.ADMIN_SEAT_FREED).exists()

        response = admin_client.post(
            reverse("admin:catalog_activityclass_change", args=[cls.pk]),
            {
                "provider": cls.provider_id,
                "term": cls.term_id,
                "title": cls.title,
                "slug": cls.slug,
                "description": cls.description,
                "extra_details": "",
                "age_min": cls.age_min,
                "age_max": cls.age_max,
                "capacity": 2,  # raised from 1
                "weekday": cls.weekday,
                "start_time": "15:00:00",
                "end_time": "16:00:00",
                "location": "",
                "status": cls.status,
                "sessions-TOTAL_FORMS": 0,
                "sessions-INITIAL_FORMS": 0,
            },
        )

        assert response.status_code == 302
        assert Notification.objects.filter(event=Event.ADMIN_SEAT_FREED).exists()
