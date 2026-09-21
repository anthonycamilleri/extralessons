import pytest
from django.urls import reverse

from apps.enrollments import services
from apps.enrollments.models import Attendance
from apps.notifications.models import Broadcast, Event, Notification

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
            {
                "classes": [cls.pk],
                "audience": Broadcast.Audience.EVERYONE,
                "subject": "Kit reminder",
                "body_html": "<p>Bring <em>boots</em>.</p>",
            },
        )

        assert response.status_code == 302
        row = Notification.objects.get(
            event=Event.BROADCAST, recipient=parent, channel="EMAIL"
        )
        assert "Kit reminder" in row.rendered_subject
        assert "Bring boots." in row.rendered_body
        assert "<em>boots</em>" in row.rendered_html

    def test_composer_loads_the_editor_and_refuses_an_empty_message(self, client):
        provider_user, cls = provider_with_class()
        client.force_login(provider_user)

        page = client.get(reverse("provider_broadcast"))
        assert b"vendor/quill/quill.js" in page.content
        assert b'data-richtext="1"' in page.content
        assert reverse("announcement_test_send").encode() in page.content

        response = client.post(
            reverse("provider_broadcast"),
            {
                "classes": [cls.pk],
                "audience": Broadcast.Audience.EVERYONE,
                "subject": "Kit",
                "body_html": "<p><br></p>",
            },
        )
        assert response.status_code == 200
        assert b"Write a message." in response.content
        assert not Notification.objects.filter(event=Event.BROADCAST).exists()

    def test_provider_can_write_to_the_waiting_list_alone(self, client):
        admin = AdminFactory()
        provider_user, cls = provider_with_class(capacity=1)
        seated = services.approve_request(services.register(ChildFactory(), cls), admin)
        waiting = services.approve_request(services.register(ChildFactory(), cls), admin)
        client.force_login(provider_user)

        response = client.post(
            reverse("provider_broadcast"),
            {
                "classes": [cls.pk],
                "audience": Broadcast.Audience.WAITLIST,
                "subject": "A place may open up",
                "body_html": "<p>Still interested?</p>",
            },
        )

        assert response.status_code == 302
        assert Broadcast.objects.get().audience == Broadcast.Audience.WAITLIST
        sent_to = set(
            Notification.objects.filter(event=Event.BROADCAST).values_list("recipient", flat=True)
        )
        assert sent_to == {waiting.child.guardians.first().pk}
        assert seated.child.guardians.first().pk not in sent_to

    def test_provider_can_write_to_a_cancelled_class(self, client):
        """The picker has always listed cancelled classes; before the third
        audience existed, writing to one reached nobody."""
        admin = AdminFactory()
        provider_user, cls = provider_with_class()
        parent = UserFactory()
        services.approve_request(services.register(ChildFactory(parent=parent), cls), admin)
        services.cancel_class(cls)
        client.force_login(provider_user)

        def send(audience):
            Notification.objects.filter(event=Event.BROADCAST).delete()
            return client.post(
                reverse("provider_broadcast"),
                {
                    "classes": [cls.pk],
                    "audience": audience,
                    "subject": "Sorry about the class",
                    "body_html": "<p>We could not run it.</p>",
                },
            )

        send(Broadcast.Audience.EVERYONE)
        assert not Notification.objects.filter(event=Event.BROADCAST).exists()

        send(Broadcast.Audience.EVER_REGISTERED)
        assert Notification.objects.filter(
            event=Event.BROADCAST, recipient=parent, channel="EMAIL"
        ).exists()

    def test_broadcast_form_rejects_other_providers_class(self, client):
        provider_user, _ = provider_with_class()
        other_cls = ActivityClassFactory()
        client.force_login(provider_user)

        response = client.post(
            reverse("provider_broadcast"),
            {
                "classes": [other_cls.pk],
                "audience": Broadcast.Audience.EVERYONE,
                "subject": "Hijack",
                "body_html": "<p>nope</p>",
            },
        )

        assert response.status_code == 200  # form redisplayed with errors
        assert not Notification.objects.filter(event=Event.BROADCAST).exists()


def _lesson_today(cls_kwargs=None, days_ago=0):
    """A provider's published class whose first generated session is `days_ago` days back."""
    import datetime

    from django.utils import timezone

    from apps.catalog.models import generate_sessions

    date = timezone.localdate() - datetime.timedelta(days=days_ago)
    provider_user, cls = provider_with_class(
        weekday=date.weekday(), **{"capacity": 5, **(cls_kwargs or {})}
    )
    generate_sessions(cls)
    return provider_user, cls, cls.sessions.get(date=date)


class TestTodayPanel:
    def test_home_offers_todays_register_then_shows_it_taken(self, client):
        admin = AdminFactory()
        provider_user, cls, session = _lesson_today({"title": "Monday Chess"})
        children = [ChildFactory() for _ in range(3)]
        for child in children:
            services.approve_request(services.register(child, cls), admin)
        client.force_login(provider_user)

        content = client.get(reverse("provider_home")).content.decode()
        url = reverse("provider_attendance", args=[cls.pk, session.pk])
        assert f'href="{url}?back=home"' in content
        assert "Take attendance" in content
        assert "No lessons today" not in content

        # Saving from the Today panel lands back on it, with the count.
        response = client.post(url + "?back=home", {"present": [children[0].pk, children[1].pk]})
        assert response.status_code == 302
        assert response.url == reverse("provider_home")
        content = client.get(reverse("provider_home")).content.decode()
        assert "2 of 3 present" in content
        assert "Taken" in content

    def test_home_lists_registers_never_taken_last_week(self, client):
        admin = AdminFactory()
        provider_user, cls, forgotten = _lesson_today({"title": "Forgotten Judo"}, days_ago=3)
        services.approve_request(services.register(ChildFactory(), cls), admin)
        # A cancelled lesson has no register to take, and one long ago is left alone.
        cancelled = cls.sessions.filter(date__lt=forgotten.date).first()
        if cancelled is None:
            # The term started a week ago; make an older lesson by hand.
            import datetime

            cancelled = cls.sessions.create(
                date=forgotten.date - datetime.timedelta(days=1), cancelled=True
            )
        else:
            cancelled.cancelled = True
            cancelled.save()
        client.force_login(provider_user)

        content = client.get(reverse("provider_home")).content.decode()
        assert "Registers still to take" in content
        assert reverse("provider_attendance", args=[cls.pk, forgotten.pk]) in content
        assert reverse("provider_attendance", args=[cls.pk, cancelled.pk]) not in content

        client.post(reverse("provider_attendance", args=[cls.pk, forgotten.pk]), {"present": []})
        content = client.get(reverse("provider_home")).content.decode()
        assert "Registers still to take" not in content

    def test_home_without_a_lesson_today_names_the_next_one(self, client):
        provider_user, cls, _ = _lesson_today({"title": "Tomorrow Tennis"}, days_ago=-1)
        client.force_login(provider_user)
        content = client.get(reverse("provider_home")).content.decode()
        assert "No lessons today" in content
        assert "Tomorrow Tennis" in content


class TestRegisterState:
    def test_class_page_shows_each_lessons_register_state(self, client):
        admin = AdminFactory()
        provider_user, cls, session = _lesson_today(days_ago=2)
        provider_user.first_name, provider_user.last_name = "Anna", "Coach"
        provider_user.save()
        children = [ChildFactory() for _ in range(3)]
        for child in children:
            services.approve_request(services.register(child, cls), admin)
        client.force_login(provider_user)

        content = client.get(reverse("provider_class", args=[cls.pk])).content.decode()
        assert "Not taken" in content
        assert "Take attendance" in content
        # The partial's comment must stay a comment.
        assert "{#" not in content and "{% comment" not in content

        client.post(
            reverse("provider_attendance", args=[cls.pk, session.pk]),
            {"present": [children[0].pk, children[2].pk]},
        )
        content = client.get(reverse("provider_class", args=[cls.pk])).content.decode()
        assert "Taken · 2 of 3 present" in content
        assert "by Anna Coach" in content
        assert "Edit attendance" in content
        # Nothing to take any more for that lesson; the next one is still planned.
        assert "Not taken" not in content
        assert "Planned" in content

    def test_register_page_ticks_everyone_first_and_navigates_between_lessons(self, client):
        admin = AdminFactory()
        provider_user, cls, session = _lesson_today()
        child = ChildFactory(first_name="Solo", may_leave_alone=True)
        services.approve_request(services.register(child, cls), admin)
        client.force_login(provider_user)

        response = client.get(reverse("provider_attendance", args=[cls.pk, session.pk]))
        content = response.content.decode()
        assert response.context["present_count"] == 1
        assert "May leave alone" in content
        assert "Everyone starts as present" in content
        next_session = cls.sessions.filter(date__gt=session.date).first()
        assert reverse("provider_attendance", args=[cls.pk, next_session.pk]) in content
        assert "Back to " in content


class TestManifest:
    def test_manifest_carries_the_school_name(self, client):
        from apps.accounts.models import SiteConfig

        config = SiteConfig.get()
        config.school_name = "Test School"
        config.save()
        response = client.get("/manifest.webmanifest")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/manifest+json")
        assert response.json()["name"] == "Test School Activities"
        assert response.json()["display"] == "standalone"
