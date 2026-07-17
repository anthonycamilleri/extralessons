import datetime

import pytest
from django.utils import timezone

from apps.enrollments import services
from apps.enrollments.models import Enrollment
from apps.enrollments.services import EnrollmentError
from apps.notifications.models import Event, Notification

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    ChildFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def notified(event, enrollment=None):
    qs = Notification.objects.filter(event=event)
    if enrollment is not None:
        qs = qs.filter(enrollment=enrollment)
    return qs


class TestRegister:
    def test_register_creates_requested_and_notifies(self):
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        cls = ActivityClassFactory()
        AdminFactory()  # someone to receive the admin alert

        enrollment = services.register(child, cls)

        assert enrollment.status == Enrollment.Status.REQUESTED
        # receipt to the guardian (email row always created, WA row skipped)
        emails = notified(Event.ENROLLMENT_REQUESTED, enrollment).filter(
            channel=Notification.Channel.EMAIL, recipient=parent
        )
        assert emails.count() == 1
        assert emails.first().status == Notification.Status.PENDING
        assert notified(Event.ADMIN_NEW_REQUEST).count() == 1

    def test_register_notifies_all_guardians(self):
        parent1, parent2 = UserFactory(), UserFactory()
        child = ChildFactory(parent=parent1)
        child.guardian_links.create(user=parent2)
        cls = ActivityClassFactory()

        enrollment = services.register(child, cls)

        recipients = set(
            notified(Event.ENROLLMENT_REQUESTED, enrollment).values_list(
                "recipient", flat=True
            )
        )
        assert recipients == {parent1.pk, parent2.pk}

    def test_duplicate_active_registration_blocked(self):
        child = ChildFactory()
        cls = ActivityClassFactory()
        services.register(child, cls)
        with pytest.raises(EnrollmentError, match="already has an active registration"):
            services.register(child, cls)

    def test_reregistration_after_cancel_allowed(self):
        child = ChildFactory()
        cls = ActivityClassFactory()
        first = services.register(child, cls)
        services.cancel(first, Enrollment.CancelReason.PARENT)
        second = services.register(child, cls)
        assert second.pk != first.pk
        assert child.enrollments.count() == 2

    def test_age_range_enforced(self):
        child = ChildFactory(
            date_of_birth=timezone.localdate() - datetime.timedelta(days=365 * 3)
        )
        cls = ActivityClassFactory(age_min=5, age_max=8)
        with pytest.raises(EnrollmentError, match="ages 5–8"):
            services.register(child, cls)

    def test_draft_class_not_registrable(self):
        cls = ActivityClassFactory(status="DRAFT")
        with pytest.raises(EnrollmentError):
            services.register(ChildFactory(), cls)


class TestApproval:
    def test_approve_enrolls_when_seat_free(self):
        admin = AdminFactory()
        enrollment = services.register(ChildFactory(), ActivityClassFactory(capacity=1))
        enrollment = services.approve_request(enrollment, admin)
        assert enrollment.status == Enrollment.Status.ENROLLED
        assert enrollment.decided_by == admin
        assert notified(Event.REGISTRATION_CONFIRMED, enrollment).exists()

    def test_approve_waitlists_when_full(self):
        admin = AdminFactory()
        cls = ActivityClassFactory(capacity=1)
        first = services.approve_request(services.register(ChildFactory(), cls), admin)
        second = services.approve_request(services.register(ChildFactory(), cls), admin)
        assert first.status == Enrollment.Status.ENROLLED
        assert second.status == Enrollment.Status.WAITLISTED
        assert second.waitlisted_at is not None
        assert notified(Event.WAITLISTED, second).exists()

    def test_reject_cancels_with_reason(self):
        admin = AdminFactory()
        enrollment = services.register(ChildFactory(), ActivityClassFactory())
        enrollment = services.reject_request(enrollment, admin)
        assert enrollment.status == Enrollment.Status.CANCELLED
        assert enrollment.cancel_reason == Enrollment.CancelReason.REQUEST_REJECTED
        assert notified(Event.REQUEST_REJECTED, enrollment).exists()

    def test_double_approve_rejected(self):
        admin = AdminFactory()
        enrollment = services.register(ChildFactory(), ActivityClassFactory())
        services.approve_request(enrollment, admin)
        with pytest.raises(EnrollmentError, match="already been handled"):
            services.approve_request(enrollment, admin)


def _full_class_with_waitlist(admin, capacity=1):
    """Class at capacity with one waitlisted child; returns (cls, enrolled, waitlisted)."""
    cls = ActivityClassFactory(capacity=capacity)
    enrolled = [
        services.approve_request(services.register(ChildFactory(), cls), admin)
        for _ in range(capacity)
    ]
    waitlisted = services.approve_request(services.register(ChildFactory(), cls), admin)
    assert waitlisted.status == Enrollment.Status.WAITLISTED
    return cls, enrolled, waitlisted


class TestOfferFlow:
    def test_offer_blocked_while_full(self):
        admin = AdminFactory()
        cls, enrolled, waitlisted = _full_class_with_waitlist(admin)
        with pytest.raises(EnrollmentError, match="No free seats"):
            services.offer_seat(waitlisted, admin)

    def test_cancel_frees_seat_then_offer_confirm_enrolls(self):
        admin = AdminFactory()
        cls, enrolled, waitlisted = _full_class_with_waitlist(admin)

        services.cancel(enrolled[0], Enrollment.CancelReason.PARENT)
        assert notified(Event.ADMIN_SEAT_FREED).exists()

        offered = services.offer_seat(waitlisted, admin)
        assert offered.status == Enrollment.Status.OFFERED
        assert offered.offer_expires_at is not None
        assert notified(Event.WAITLIST_OFFER, offered).exists()

        confirmed = services.confirm_offer(offered)
        assert confirmed.status == Enrollment.Status.ENROLLED
        assert confirmed.promoted_from_waitlist is True

    def test_outstanding_offer_reserves_seat(self):
        admin = AdminFactory()
        cls, enrolled, waitlisted = _full_class_with_waitlist(admin)
        other_waitlisted = services.approve_request(
            services.register(ChildFactory(), cls), admin
        )
        services.cancel(enrolled[0], Enrollment.CancelReason.PARENT)
        services.offer_seat(waitlisted, admin)
        # seat now held by the offer — a second offer must be blocked
        with pytest.raises(EnrollmentError, match="No free seats"):
            services.offer_seat(other_waitlisted, admin)

    def test_decline_frees_seat_and_alerts_admin(self):
        admin = AdminFactory()
        cls, enrolled, waitlisted = _full_class_with_waitlist(admin)
        services.cancel(enrolled[0], Enrollment.CancelReason.PARENT)
        offered = services.offer_seat(waitlisted, admin)

        declined = services.decline_offer(offered)
        assert declined.status == Enrollment.Status.CANCELLED
        assert declined.cancel_reason == Enrollment.CancelReason.OFFER_DECLINED
        assert notified(Event.ADMIN_OFFER_LAPSED).exists()
        cls.refresh_from_db()
        assert cls.places_free_now() == 1

    def test_expire_offers_sweep(self):
        admin = AdminFactory()
        cls, enrolled, waitlisted = _full_class_with_waitlist(admin)
        services.cancel(enrolled[0], Enrollment.CancelReason.PARENT)
        offered = services.offer_seat(waitlisted, admin)

        assert services.expire_offers() == 0  # not expired yet

        Enrollment.objects.filter(pk=offered.pk).update(
            offer_expires_at=timezone.now() - datetime.timedelta(minutes=1)
        )
        assert services.expire_offers() == 1
        offered.refresh_from_db()
        assert offered.status == Enrollment.Status.CANCELLED
        assert offered.cancel_reason == Enrollment.CancelReason.OFFER_EXPIRED
        assert notified(Event.OFFER_EXPIRED, offered).exists()

    def test_confirm_after_expiry_rejected(self):
        admin = AdminFactory()
        cls, enrolled, waitlisted = _full_class_with_waitlist(admin)
        services.cancel(enrolled[0], Enrollment.CancelReason.PARENT)
        offered = services.offer_seat(waitlisted, admin)
        Enrollment.objects.filter(pk=offered.pk).update(
            offer_expires_at=timezone.now() - datetime.timedelta(minutes=1)
        )
        offered.refresh_from_db()
        with pytest.raises(EnrollmentError, match="expired"):
            services.confirm_offer(offered)


class TestClassCancellation:
    def test_cancel_class_cancels_active_and_notifies(self):
        admin = AdminFactory()
        cls = ActivityClassFactory(capacity=1)
        enrolled = services.approve_request(services.register(ChildFactory(), cls), admin)
        waitlisted = services.approve_request(services.register(ChildFactory(), cls), admin)

        services.cancel_class(cls)

        cls.refresh_from_db()
        assert cls.status == "CANCELLED"
        for e in (enrolled, waitlisted):
            e.refresh_from_db()
            assert e.status == Enrollment.Status.CANCELLED
            assert e.cancel_reason == Enrollment.CancelReason.CLASS_CANCELLED
        assert notified(Event.CLASS_CANCELLED).count() >= 2

    def test_notifications_queued_once_per_transition(self):
        admin = AdminFactory()
        cls = ActivityClassFactory(capacity=1)
        enrollment = services.register(ChildFactory(), cls)
        services.approve_request(enrollment, admin)
        # one guardian => exactly one email + one (skipped) WhatsApp row per event
        for event in (Event.ENROLLMENT_REQUESTED, Event.REGISTRATION_CONFIRMED):
            assert (
                notified(event, enrollment)
                .filter(channel=Notification.Channel.EMAIL)
                .count()
                == 1
            )
