"""Regression tests for the issues found in the full code review."""
import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import GuardianInvite
from apps.catalog.models import ActivityClass
from apps.enrollments import services
from apps.enrollments.models import Enrollment
from apps.notifications import worker
from apps.notifications.models import Event, Notification, NotificationTemplate
from apps.notifications.services import create_broadcast
from apps.notifications.models import Broadcast

from .factories import ActivityClassFactory, AdminFactory, ChildFactory, UserFactory

pytestmark = pytest.mark.django_db


def _expire(enrollment):
    Enrollment.objects.filter(pk=enrollment.pk).update(
        offer_expires_at=timezone.now() - datetime.timedelta(minutes=1)
    )
    enrollment.refresh_from_db()
    return enrollment


class TestExpiredOffersDontBlockSeats:
    """A dead offer must never deadlock a seat, even with the worker down."""

    def test_offer_seat_succeeds_over_expired_offer(self):
        admin = AdminFactory()
        cls = ActivityClassFactory(capacity=1)
        first = services.approve_request(services.register(ChildFactory(), cls), admin)
        w1 = services.approve_request(services.register(ChildFactory(), cls), admin)
        w2 = services.approve_request(services.register(ChildFactory(), cls), admin)
        services.cancel(first, Enrollment.CancelReason.PARENT)
        offered = _expire(services.offer_seat(w1, admin))

        # no expiry sweep ran — the admin can still offer the seat to w2
        services.offer_seat(w2, admin)

        offered.refresh_from_db()
        assert offered.status == Enrollment.Status.CANCELLED
        assert offered.cancel_reason == Enrollment.CancelReason.OFFER_EXPIRED
        w2.refresh_from_db()
        assert w2.status == Enrollment.Status.OFFERED

    def test_catalogue_counts_ignore_expired_offers(self):
        admin = AdminFactory()
        cls = ActivityClassFactory(capacity=1)
        first = services.approve_request(services.register(ChildFactory(), cls), admin)
        waitlisted = services.approve_request(services.register(ChildFactory(), cls), admin)
        services.cancel(first, Enrollment.CancelReason.PARENT)
        _expire(services.offer_seat(waitlisted, admin))

        annotated = ActivityClass.objects.with_counts().get(pk=cls.pk)
        assert annotated.places_free == 1


class TestClassStateGuards:
    def test_approve_offer_confirm_blocked_on_cancelled_class(self):
        admin = AdminFactory()
        cls = ActivityClassFactory(capacity=2)
        requested = services.register(ChildFactory(), cls)
        enrolled = services.approve_request(services.register(ChildFactory(), cls), admin)
        services.cancel_class(cls)

        requested.refresh_from_db()
        # cancel_class already cancelled it; a stale admin click must not revive it
        with pytest.raises(services.EnrollmentError):
            services.approve_request(requested, admin)

    def test_confirm_offer_blocked_after_class_cancelled(self):
        admin = AdminFactory()
        cls = ActivityClassFactory(capacity=1)
        first = services.approve_request(services.register(ChildFactory(), cls), admin)
        waitlisted = services.approve_request(services.register(ChildFactory(), cls), admin)
        services.cancel(first, Enrollment.CancelReason.PARENT)
        offered = services.offer_seat(waitlisted, admin)

        ActivityClass.objects.filter(pk=cls.pk).update(
            status=ActivityClass.Status.CANCELLED
        )
        with pytest.raises(services.EnrollmentError, match="no longer open"):
            services.confirm_offer(offered)


class TestCapacityGuards:
    def test_admin_form_rejects_capacity_below_seats_taken(self, admin_client):
        admin = AdminFactory()
        cls = ActivityClassFactory(capacity=3)
        for _ in range(2):
            services.approve_request(services.register(ChildFactory(), cls), admin)

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
                "capacity": 1,  # below the 2 seats taken
                "weekday": cls.weekday,
                "start_time": "15:00:00",
                "end_time": "16:00:00",
                "location": "",
                "sessions-TOTAL_FORMS": 0,
                "sessions-INITIAL_FORMS": 0,
            },
        )

        assert response.status_code == 200  # form redisplayed with error
        cls.refresh_from_db()
        assert cls.capacity == 3

    def test_capacity_raise_via_plain_save_alerts_admins(self):
        admin = AdminFactory()
        cls = ActivityClassFactory(capacity=1)
        services.approve_request(services.register(ChildFactory(), cls), admin)
        services.approve_request(services.register(ChildFactory(), cls), admin)  # waitlisted

        cls.capacity = 2
        cls.save()  # shell/script path, not the admin

        assert Notification.objects.filter(event=Event.ADMIN_SEAT_FREED).exists()

    def test_archive_action_skips_classes_with_active_enrollments(self, admin_client):
        admin = AdminFactory()
        busy = ActivityClassFactory()
        services.approve_request(services.register(ChildFactory(), busy), admin)
        empty = ActivityClassFactory()

        admin_client.post(
            reverse("admin:catalog_activityclass_changelist"),
            {"action": "archive_classes", "_selected_action": [busy.pk, empty.pk]},
        )

        busy.refresh_from_db()
        empty.refresh_from_db()
        assert busy.status != ActivityClass.Status.ARCHIVED
        assert empty.status == ActivityClass.Status.ARCHIVED


class TestInviteSecurity:
    def _invite(self, email="coparent@family.test"):
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        return GuardianInvite.objects.create(
            child=child, email=email, invited_by=parent
        )

    def test_wrong_account_cannot_accept_forwarded_invite(self, client):
        invite = self._invite()
        interloper = UserFactory(email="other@family.test")
        client.force_login(interloper)

        client.post(reverse("accept_guardian_invite", args=[invite.token]))

        invite.refresh_from_db()
        assert invite.accepted_at is None
        assert not invite.child.guardians.filter(pk=interloper.pk).exists()

    def test_matching_account_can_accept(self, client):
        invite = self._invite(email="right@family.test")
        invited = UserFactory(email="right@family.test")
        client.force_login(invited)

        client.post(reverse("accept_guardian_invite", args=[invite.token]))

        invite.refresh_from_db()
        assert invite.accepted_at is not None
        assert invite.child.guardians.filter(pk=invited.pk).exists()

    def test_expired_invite_404s(self, client):
        invite = self._invite()
        GuardianInvite.objects.filter(pk=invite.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=30)
        )
        assert client.get(reverse("invite_landing", args=[invite.token])).status_code == 404

    def test_signup_honors_next_for_invite_flow(self, client):
        next_url = reverse("accept_guardian_invite", args=["sometoken"])
        response = client.post(
            f"{reverse('signup')}?next={next_url}",
            {
                "email": "new@parent.test",
                "first_name": "New",
                "last_name": "Parent",
                "phone_e164": "",
                "password1": "s3cure-pass-123",
                "password2": "s3cure-pass-123",
                "next": next_url,
            },
        )
        assert response.status_code == 302
        assert response.url == next_url


class TestNotificationContent:
    def test_email_body_is_not_html_escaped(self):
        parent = UserFactory(first_name="Pat", last_name="O'Brien")
        child = ChildFactory(parent=parent)
        cls = ActivityClassFactory(title="Arts & Crafts")
        services.register(child, cls)

        row = Notification.objects.get(
            recipient=parent, channel=Notification.Channel.EMAIL
        )
        assert "Arts & Crafts" in row.rendered_body
        assert "&amp;" not in row.rendered_body
        assert "&#x27;" not in row.rendered_body

    def test_broadcast_whatsapp_params_are_class_agnostic(self):
        NotificationTemplate.objects.filter(event=Event.BROADCAST).update(
            wa_template_name="school_announcement",
            wa_param_order=["subject", "body"],
        )
        parent = UserFactory(notify_whatsapp=True, phone_e164="+35699000001")
        child = ChildFactory(parent=parent)
        cls_a, cls_b = ActivityClassFactory(), ActivityClassFactory()
        services.register(child, cls_a)
        services.register(child, cls_b)

        _, count = create_broadcast(
            sender=AdminFactory(),
            scope=Broadcast.Scope.SELECTED_CLASSES,
            subject="Show night",
            body="Doors at 6.",
            classes=[cls_a, cls_b],
        )

        assert count == 1  # one guardian, one message despite two classes
        row = Notification.objects.get(
            event=Event.BROADCAST, channel=Notification.Channel.WHATSAPP
        )
        assert row.wa_params == ["Show night", "Doors at 6."]

    def test_worker_rechecks_optout_at_delivery_time(self):
        parent = UserFactory()
        services.register(ChildFactory(parent=parent), ActivityClassFactory())
        parent.notify_email = False
        parent.save()

        worker.run_once()

        row = Notification.objects.get(
            recipient=parent, channel=Notification.Channel.EMAIL
        )
        assert row.status == Notification.Status.SKIPPED
        assert "before delivery" in row.skip_reason


class TestAttendanceRobustness:
    def test_tampered_checkbox_value_does_not_500(self, client):
        from .factories import ProviderUserFactory

        admin = AdminFactory()
        provider_user = ProviderUserFactory()
        cls = ActivityClassFactory()
        cls.provider.members.add(provider_user)
        child = ChildFactory()
        services.approve_request(services.register(child, cls), admin)
        from apps.catalog.models import generate_sessions

        generate_sessions(cls)
        session = cls.sessions.first()
        client.force_login(provider_user)

        response = client.post(
            reverse("provider_attendance", args=[cls.pk, session.pk]),
            {"present": ["abc", str(child.pk)]},
        )

        assert response.status_code == 302  # saved, bad value ignored


class TestSessionReconciliation:
    def test_weekday_change_removes_future_ghost_sessions(self):
        from apps.catalog.models import generate_sessions

        cls = ActivityClassFactory(weekday=0)  # Mondays
        generate_sessions(cls)
        assert all(s.date.weekday() == 0 for s in cls.sessions.all())

        cls.weekday = 2  # move to Wednesdays
        cls.save()
        generate_sessions(cls)

        today = timezone.localdate()
        future = [s for s in cls.sessions.all() if s.date >= today]
        assert future
        assert all(s.date.weekday() == 2 for s in future)
