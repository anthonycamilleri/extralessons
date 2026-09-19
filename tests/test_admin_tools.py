import pytest
from django.urls import reverse

from apps.catalog.models import ActivityClass
from apps.enrollments import services
from apps.enrollments.models import Enrollment
from apps.notifications.models import Broadcast, Event, Notification

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    SuperAdminFactory,
    ChildFactory,
    TermFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


class TestAdminBroadcast:
    def test_broadcast_to_all_classes(self, client):
        admin = SuperAdminFactory()
        parent = UserFactory()
        cls = ActivityClassFactory()
        services.register(ChildFactory(parent=parent), cls)
        client.force_login(admin)

        response = client.post(
            reverse("admin:notifications_broadcast_add"),
            {
                "scope": "ALL_CLASSES",
                "audience": Broadcast.Audience.EVERYONE,
                "subject": "School closed Friday",
                "body_html": "<p>Public <strong>holiday</strong>.</p>",
                "_save": "1",
            },
        )

        assert response.status_code == 302
        row = Notification.objects.get(
            event=Event.BROADCAST, recipient=parent, channel=Notification.Channel.EMAIL
        )
        assert "<strong>holiday</strong>" in row.rendered_html
        assert "Public holiday." in row.rendered_body
        broadcast = Broadcast.objects.get()
        assert broadcast.sender == admin
        assert broadcast.body == "Public holiday."
        assert broadcast.body_html == "<p>Public <strong>holiday</strong>.</p>"

    def test_message_is_cleaned_before_it_is_stored(self, client):
        client.force_login(SuperAdminFactory())
        client.post(
            reverse("admin:notifications_broadcast_add"),
            {
                "scope": "ALL_CLASSES",
                "audience": Broadcast.Audience.EVERYONE,
                "subject": "Kit",
                "body_html": '<p onclick="x()">Boots</p><script>alert(1)</script>'
                '<img src="data:image/png;base64,AAA">',
                "_save": "1",
            },
        )
        broadcast = Broadcast.objects.get()
        assert broadcast.body_html == "<p>Boots</p>"
        assert broadcast.body == "Boots"

    def test_empty_message_is_refused(self, client):
        client.force_login(SuperAdminFactory())
        response = client.post(
            reverse("admin:notifications_broadcast_add"),
            {"scope": "ALL_CLASSES", "subject": "Kit", "body_html": "<p><br></p>", "_save": "1"},
        )
        assert response.status_code == 200
        assert b"Write a message." in response.content
        assert not Broadcast.objects.exists()

    def test_composer_loads_the_editor(self, client):
        client.force_login(SuperAdminFactory())
        response = client.get(reverse("admin:notifications_broadcast_add"))
        assert b"vendor/quill/quill.js" in response.content
        assert b"js/richtext.js" in response.content
        assert b'data-richtext="1"' in response.content
        assert reverse("announcement_image_upload").encode() in response.content
        assert reverse("announcement_test_send").encode() in response.content

    def test_sent_announcement_shows_its_formatting(self, client):
        admin = SuperAdminFactory()
        client.force_login(admin)
        broadcast = Broadcast.objects.create(
            sender=admin,
            scope="ALL_CLASSES",
            subject="Kit",
            body="Bring boots",
            body_html="<p>Bring <strong>boots</strong></p>",
        )
        response = client.get(
            reverse("admin:notifications_broadcast_change", args=[broadcast.pk])
        )
        assert response.status_code == 200
        assert b"<p>Bring <strong>boots</strong></p>" in response.content

    def test_plain_text_history_keeps_its_line_breaks(self, client):
        admin = SuperAdminFactory()
        client.force_login(admin)
        broadcast = Broadcast.objects.create(
            sender=admin, scope="ALL_CLASSES", subject="Old", body="Line one\nLine two"
        )
        response = client.get(
            reverse("admin:notifications_broadcast_change", args=[broadcast.pk])
        )
        assert b"Line one<br>Line two" in response.content

    def test_selected_scope_requires_classes(self, client):
        admin = AdminFactory()
        ActivityClassFactory().administrators.add(admin)
        client.force_login(admin)
        response = client.post(
            reverse("admin:notifications_broadcast_add"),
            {"scope": "SELECTED_CLASSES", "subject": "x", "body_html": "<p>y</p>", "_save": "1"},
        )
        assert response.status_code == 200
        assert b"Pick at least one class" in response.content
        assert not Broadcast.objects.exists()

    def test_broadcast_page_forbidden_for_parents(self, client):
        client.force_login(UserFactory())
        response = client.get(reverse("admin:notifications_broadcast_add"))
        assert response.status_code == 302
        assert response.url.startswith(reverse("admin:login"))


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


class TestClassAnnouncement:
    """The composer on the class itself: one class, already addressed.

    Its reason to exist is the class the main composer cannot reach — a
    cancelled one, or one whose term is over — without those classes having
    to appear in that composer's picker.
    """

    def announce(self, client, cls, **fields):
        return client.post(
            reverse("admin:catalog_activityclass_announce", args=[cls.pk]),
            {
                "audience": Broadcast.Audience.EVERYONE,
                "subject": "Room change",
                "body_html": "<p>We are in the <strong>hall</strong>.</p>",
                **fields,
            },
        )

    def test_the_class_list_offers_it_per_class(self, client):
        cls = ActivityClassFactory()
        client.force_login(SuperAdminFactory())
        page = client.get(reverse("admin:catalog_activityclass_changelist")).content.decode()
        assert reverse("admin:catalog_activityclass_announce", args=[cls.pk]) in page

    def test_it_reaches_that_class_only(self, client):
        parent, stranger = UserFactory(), UserFactory()
        cls, other = ActivityClassFactory(), ActivityClassFactory()
        services.register(ChildFactory(parent=parent), cls)
        services.register(ChildFactory(parent=stranger), other)
        client.force_login(SuperAdminFactory())

        response = self.announce(client, cls)

        assert response.status_code == 302
        broadcast = Broadcast.objects.get()
        assert broadcast.scope == Broadcast.Scope.SELECTED_CLASSES
        assert list(broadcast.classes.all()) == [cls]
        sent_to = set(
            Notification.objects.filter(
                event=Event.BROADCAST, channel=Notification.Channel.EMAIL
            ).values_list("recipient", flat=True)
        )
        assert sent_to == {parent.pk}

    def test_a_cancelled_class_has_nobody_with_a_live_place(self, client):
        """The gap this page fills: cancelling cancels every place in the class."""
        parent = UserFactory()
        cls = ActivityClassFactory()
        services.register(ChildFactory(parent=parent), cls)
        services.cancel_class(cls)
        client.force_login(SuperAdminFactory())

        self.announce(client, cls, audience=Broadcast.Audience.EVERYONE)

        assert not Notification.objects.filter(event=Event.BROADCAST).exists()

    def test_a_cancelled_class_can_still_be_written_to(self, client):
        parent = UserFactory()
        cls = ActivityClassFactory()
        services.register(ChildFactory(parent=parent), cls)
        services.cancel_class(cls)
        client.force_login(SuperAdminFactory())

        self.announce(client, cls, audience=Broadcast.Audience.EVER_REGISTERED)

        row = Notification.objects.get(
            event=Event.BROADCAST, recipient=parent, channel=Notification.Channel.EMAIL
        )
        assert "<strong>hall</strong>" in row.rendered_html
        assert Broadcast.objects.get().audience == Broadcast.Audience.EVER_REGISTERED

    def test_each_audience_says_how_many_families_it_reaches(self, client):
        cls = ActivityClassFactory()
        services.register(ChildFactory(parent=UserFactory()), cls)
        services.cancel_class(cls)
        client.force_login(SuperAdminFactory())

        page = client.get(
            reverse("admin:catalog_activityclass_announce", args=[cls.pk])
        ).content.decode()

        assert "Everyone with a live place — 0 families" in page
        assert "cancelled places included — 1 family" in page

    def test_a_class_whose_term_is_over_can_still_be_written_to(self, client):
        """The main composer lists the active term only; this page has no picker."""
        parent = UserFactory()
        cls = ActivityClassFactory()
        services.register(ChildFactory(parent=parent), cls)
        cls.term.is_active = False
        cls.term.save(update_fields=["is_active"])
        client.force_login(SuperAdminFactory())

        assert self.announce(client, cls).status_code == 302
        assert Notification.objects.filter(
            event=Event.BROADCAST, recipient=parent, channel=Notification.Channel.EMAIL
        ).exists()

    def test_a_send_that_reached_nobody_says_so(self, client):
        cls = ActivityClassFactory()
        client.force_login(SuperAdminFactory())

        response = self.announce(client, cls, audience=Broadcast.Audience.WAITLIST)

        assert b"was not sent to anyone" in client.get(response.url).content

    def test_the_composer_loads_the_editor(self, client):
        cls = ActivityClassFactory()
        client.force_login(SuperAdminFactory())
        page = client.get(
            reverse("admin:catalog_activityclass_announce", args=[cls.pk])
        ).content
        assert b"vendor/quill/quill.js" in page
        assert b'data-richtext="1"' in page
        assert reverse("announcement_test_send").encode() in page

    def test_an_empty_message_is_refused(self, client):
        cls = ActivityClassFactory()
        client.force_login(SuperAdminFactory())

        response = self.announce(client, cls, body_html="<p><br></p>")

        assert response.status_code == 200
        assert b"Write a message." in response.content
        assert not Broadcast.objects.exists()

    def test_an_admin_cannot_write_to_a_class_that_is_not_theirs(self, client):
        admin = AdminFactory()
        mine, theirs = ActivityClassFactory(), ActivityClassFactory()
        mine.administrators.add(admin)
        client.force_login(admin)

        assert self.announce(client, mine).status_code == 302
        assert self.announce(client, theirs).status_code == 404
        assert list(Broadcast.objects.get().classes.all()) == [mine]

    def test_it_is_closed_to_parents(self, client):
        cls = ActivityClassFactory()
        client.force_login(UserFactory())
        response = client.get(
            reverse("admin:catalog_activityclass_announce", args=[cls.pk])
        )
        assert response.status_code == 302
        assert response.url.startswith(reverse("admin:login"))
