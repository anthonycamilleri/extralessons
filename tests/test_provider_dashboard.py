import pytest
from django.urls import reverse

from apps.enrollments import services
from apps.enrollments.models import Attendance
from apps.notifications.models import Event, Notification

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    ChildFactory,
    ProviderUserFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def provider_with_class(**cls_kwargs):
    provider_user = ProviderUserFactory()
    cls = ActivityClassFactory(**cls_kwargs)
    cls.provider.members.add(provider_user)
    return provider_user, cls


class TestProviderScoping:
    def test_provider_sees_only_own_classes(self, client):
        provider_user, own_cls = provider_with_class(title="My Football")
        ActivityClassFactory(title="Someone Elses Chess")
        client.force_login(provider_user)

        response = client.get(reverse("provider_home"))

        content = response.content.decode()
        assert "My Football" in content
        assert "Someone Elses Chess" not in content

    def test_provider_cannot_open_other_providers_class(self, client):
        provider_user, _ = provider_with_class()
        other_cls = ActivityClassFactory()
        client.force_login(provider_user)
        assert client.get(reverse("provider_class", args=[other_cls.pk])).status_code == 404

    def test_parent_cannot_open_provider_pages(self, client):
        client.force_login(UserFactory())
        assert client.get(reverse("provider_home")).status_code == 403


class TestRoster:
    def test_roster_shows_enrolled_children_and_notes(self, client):
        admin = AdminFactory()
        provider_user, cls = provider_with_class()
        child = ChildFactory(first_name="Nutty", notes="Peanut allergy")
        services.approve_request(services.register(child, cls), admin)
        waiting_child = ChildFactory(first_name="Waity")
        cls2 = services.register(waiting_child, cls)

        client.force_login(provider_user)
        response = client.get(reverse("provider_class", args=[cls.pk]))

        content = response.content.decode()
        assert "Nutty" in content
        assert "Peanut allergy" in content
        # requested-but-unapproved children are not on the roster
        assert "Waity" not in content


class TestAttendance:
    def test_take_and_edit_attendance(self, client):
        admin = AdminFactory()
        provider_user, cls = provider_with_class(capacity=5)
        children = [ChildFactory() for _ in range(3)]
        for child in children:
            services.approve_request(services.register(child, cls), admin)
        from apps.catalog.models import generate_sessions

        generate_sessions(cls)
        session = cls.sessions.first()
        client.force_login(provider_user)

        url = reverse("provider_attendance", args=[cls.pk, session.pk])
        assert client.get(url).status_code == 200

        # mark first two present, third absent
        response = client.post(url, {"present": [children[0].pk, children[1].pk]})
        assert response.status_code == 302
        marks = {a.child_id: a.present for a in Attendance.objects.filter(session=session)}
        assert marks == {children[0].pk: True, children[1].pk: True, children[2].pk: False}

        # edit: now only the third is present
        client.post(url, {"present": [children[2].pk]})
        marks = {a.child_id: a.present for a in Attendance.objects.filter(session=session)}
        assert marks == {children[0].pk: False, children[1].pk: False, children[2].pk: True}

    def test_attendance_scoped_to_own_class_session(self, client):
        provider_user, cls = provider_with_class()
        other_cls = ActivityClassFactory()
        from apps.catalog.models import generate_sessions

        generate_sessions(other_cls)
        other_session = other_cls.sessions.first()
        client.force_login(provider_user)
        url = reverse("provider_attendance", args=[other_cls.pk, other_session.pk])
        assert client.get(url).status_code == 404


class TestProviderBroadcast:
    def test_broadcast_reaches_own_class_families(self, client):
        admin = AdminFactory()
        provider_user, cls = provider_with_class()
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        services.approve_request(services.register(child, cls), admin)
        client.force_login(provider_user)

        response = client.post(
            reverse("provider_broadcast"),
            {"classes": [cls.pk], "subject": "Kit reminder", "body": "Bring boots."},
        )

        assert response.status_code == 302
        row = Notification.objects.get(
            event=Event.BROADCAST, recipient=parent, channel="EMAIL"
        )
        assert "Kit reminder" in row.rendered_subject

    def test_broadcast_form_rejects_other_providers_class(self, client):
        provider_user, _ = provider_with_class()
        other_cls = ActivityClassFactory()
        client.force_login(provider_user)

        response = client.post(
            reverse("provider_broadcast"),
            {"classes": [other_cls.pk], "subject": "Hijack", "body": "nope"},
        )

        assert response.status_code == 200  # form redisplayed with errors
        assert not Notification.objects.filter(event=Event.BROADCAST).exists()
