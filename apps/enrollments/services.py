"""All enrollment state transitions live here, and only here.

Every public function runs in its own transaction and takes a row lock on the
ActivityClass (`select_for_update`) as the capacity mutex: counting seats and
changing enrollment rows always happen under the same lock, so approvals can
never oversubscribe a class however many people click at once. The one
deliberate exception is `offer_seat`: an administrator may offer a waitlisted
family a place beyond the class's capacity, and that over-allocation is their
call, not something a race produced.

Notifications are queued inside the same transaction (transactional outbox):
they commit together with the state change and are delivered by the
run_notifier worker afterwards.
"""
import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import SiteConfig
from apps.catalog.models import ActivityClass
from apps.notifications import services as notifications
from apps.notifications.models import Event

from .models import Enrollment


class EnrollmentError(Exception):
    """User-facing rule violation (shown as a form/flash error)."""


def _locked_class(activity_class_id):
    return ActivityClass.objects.select_for_update().get(pk=activity_class_id)


def _seats_taken(cls):
    """Seats currently held: enrolled children plus unexpired offers.

    Expired offers stop holding a seat immediately, so seat availability never
    depends on the notifier worker's expiry sweep being alive.
    """
    return cls.enrollments.filter(
        Q(status=Enrollment.Status.ENROLLED)
        | Q(status=Enrollment.Status.OFFERED, offer_expires_at__gte=timezone.now())
    ).count()


def _registrations(cls):
    """Live registrations: every family that has asked for a place and not
    been told no — pending requests, enrolled children, the waiting list and
    unexpired offers. The parent-facing "places available" counts against
    this, so a class cannot look open while its places are spoken for."""
    return cls.enrollments.filter(
        Q(
            status__in=[
                Enrollment.Status.REQUESTED,
                Enrollment.Status.ENROLLED,
                Enrollment.Status.WAITLISTED,
            ]
        )
        | Q(status=Enrollment.Status.OFFERED, offer_expires_at__gte=timezone.now())
    ).count()


def _require_open(cls):
    """Admission transitions are only valid into a published, active class."""
    if cls.status != ActivityClass.Status.PUBLISHED or not cls.term.is_active:
        raise EnrollmentError(
            "This class is no longer open (cancelled, archived or past its term)."
        )


def _expire_stale_offers_locked(cls):
    """Cancel this class's expired offers. Caller holds the class lock."""
    now = timezone.now()
    stale = cls.enrollments.select_for_update().filter(
        status=Enrollment.Status.OFFERED, offer_expires_at__lt=now
    )
    for enrollment in stale:
        enrollment.status = Enrollment.Status.CANCELLED
        enrollment.cancelled_at = now
        enrollment.cancel_reason = Enrollment.CancelReason.OFFER_EXPIRED
        enrollment.save()
        notifications.queue_event(Event.OFFER_EXPIRED, enrollment)
        notifications.queue_admin_event(
            Event.ADMIN_OFFER_LAPSED, enrollment, lapse_reason="not answered in time"
        )


def _validate_registration(cls, child):
    if cls.status != ActivityClass.Status.PUBLISHED:
        raise EnrollmentError("This class is not open for registration.")
    if not cls.term.is_active:
        raise EnrollmentError("Registration for this term is closed.")
    # The age range is deliberately not enforced here: it is a recommendation
    # the parent confirms past (see apps.enrollments.ages) and the school
    # weighs when it approves the request.
    if Enrollment.objects.filter(
        child=child, activity_class=cls, status__in=Enrollment.ACTIVE_STATUSES
    ).exists():
        raise EnrollmentError(
            f"{child.full_name} already has an active registration for this class."
        )


def register(child, activity_class, *, terms_accepted=False):
    """Parent requests a place. Creates a REQUESTED enrollment for admin review.

    `terms_accepted` stamps the moment the parent confirmed the terms and
    conditions; the view enforces the tick, this only records it.
    """
    with transaction.atomic():
        cls = _locked_class(activity_class.pk)
        _validate_registration(cls, child)
        enrollment = Enrollment.objects.create(
            child=child,
            activity_class=cls,
            status=Enrollment.Status.REQUESTED,
            terms_accepted_at=timezone.now() if terms_accepted else None,
        )
        notifications.queue_event(Event.ENROLLMENT_REQUESTED, enrollment)
        notifications.queue_admin_event(Event.ADMIN_NEW_REQUEST, enrollment)
    return enrollment


def approve_request(enrollment, admin_user):
    """Admin approves a request: enrolled if a seat is free, else waitlisted."""
    with transaction.atomic():
        cls = _locked_class(enrollment.activity_class_id)
        _require_open(cls)
        _expire_stale_offers_locked(cls)
        enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
        if enrollment.status != Enrollment.Status.REQUESTED:
            raise EnrollmentError("This request has already been handled.")
        now = timezone.now()
        enrollment.approved_at = now
        enrollment.decided_by = admin_user
        if _seats_taken(cls) < cls.capacity:
            enrollment.status = Enrollment.Status.ENROLLED
            enrollment.enrolled_at = now
            enrollment.save()
            notifications.queue_event(Event.REGISTRATION_CONFIRMED, enrollment)
        else:
            enrollment.status = Enrollment.Status.WAITLISTED
            enrollment.waitlisted_at = now
            enrollment.save()
            notifications.queue_event(Event.WAITLISTED, enrollment)
    return enrollment


def reject_request(enrollment, admin_user):
    with transaction.atomic():
        enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
        if enrollment.status != Enrollment.Status.REQUESTED:
            raise EnrollmentError("This request has already been handled.")
        enrollment.status = Enrollment.Status.CANCELLED
        enrollment.cancelled_at = timezone.now()
        enrollment.cancel_reason = Enrollment.CancelReason.REQUEST_REJECTED
        enrollment.decided_by = admin_user
        enrollment.save()
        notifications.queue_event(Event.REQUEST_REJECTED, enrollment)
    return enrollment


def seats_over_capacity(activity_class):
    """How many seats beyond capacity are held (0 when the class is within it).

    Positive only after an administrator has offered places past the maximum;
    approvals never take a class over.
    """
    return max(0, _seats_taken(activity_class) - activity_class.capacity)


def offer_seat(enrollment, admin_user):
    """Admin offers a seat to a chosen waitlisted family.

    Capacity is not enforced here: the roster tells the administrator when the
    class is full, and offering anyway is a legitimate decision (a class that
    can stretch by one, a sibling, a family that has waited all term). Only
    approvals are gated by capacity, so nothing over-allocates by accident.
    """
    with transaction.atomic():
        cls = _locked_class(enrollment.activity_class_id)
        _require_open(cls)
        _expire_stale_offers_locked(cls)
        enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
        if enrollment.status != Enrollment.Status.WAITLISTED:
            raise EnrollmentError("Only waitlisted registrations can receive an offer.")
        ttl_hours = SiteConfig.get().offer_ttl_hours
        now = timezone.now()
        enrollment.status = Enrollment.Status.OFFERED
        enrollment.offered_at = now
        enrollment.offer_expires_at = now + datetime.timedelta(hours=ttl_hours)
        enrollment.decided_by = admin_user
        enrollment.save()
        notifications.queue_event(Event.WAITLIST_OFFER, enrollment)
    return enrollment


def confirm_offer(enrollment):
    """Parent confirms an offered seat. The seat is already reserved."""
    with transaction.atomic():
        cls = _locked_class(enrollment.activity_class_id)
        _require_open(cls)
        enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
        if enrollment.status != Enrollment.Status.OFFERED:
            raise EnrollmentError("This offer is no longer open.")
        if enrollment.offer_expires_at and enrollment.offer_expires_at < timezone.now():
            raise EnrollmentError("This offer has expired.")
        enrollment.status = Enrollment.Status.ENROLLED
        enrollment.enrolled_at = timezone.now()
        enrollment.promoted_from_waitlist = True
        enrollment.save()
        notifications.queue_event(Event.REGISTRATION_CONFIRMED, enrollment)
    return enrollment


def decline_offer(enrollment):
    with transaction.atomic():
        _locked_class(enrollment.activity_class_id)
        enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
        if enrollment.status != Enrollment.Status.OFFERED:
            raise EnrollmentError("This offer is no longer open.")
        enrollment.status = Enrollment.Status.CANCELLED
        enrollment.cancelled_at = timezone.now()
        enrollment.cancel_reason = Enrollment.CancelReason.OFFER_DECLINED
        enrollment.save()
        notifications.queue_admin_event(
            Event.ADMIN_OFFER_LAPSED, enrollment, lapse_reason="declined by the family"
        )
    return enrollment


def expire_offers(now=None):
    """Cancel offers past their deadline. Called periodically by the worker.

    Takes the class lock (like every other transition) so it can never race a
    concurrent offer/approval working from a stale seat count.
    """
    now = now or timezone.now()
    class_ids = (
        Enrollment.objects.filter(
            status=Enrollment.Status.OFFERED, offer_expires_at__lt=now
        )
        .values_list("activity_class_id", flat=True)
        .distinct()
    )
    processed = 0
    for class_id in class_ids:
        with transaction.atomic():
            cls = _locked_class(class_id)
            before = cls.enrollments.filter(status=Enrollment.Status.OFFERED).count()
            _expire_stale_offers_locked(cls)
            after = cls.enrollments.filter(status=Enrollment.Status.OFFERED).count()
            processed += before - after
    return processed


# What the family hears when a registration ends, by who ended it and how.
_CANCEL_EVENTS = {
    Enrollment.CancelReason.PARENT: Event.WITHDRAWN,
    Enrollment.CancelReason.PARENT_REQUEST: Event.CANCELLATION_CONFIRMED,
}


def cancel(enrollment, reason, actor=None):
    """Cancel any active enrollment (parent withdrawal or admin action).

    The parent-facing email follows the reason: a withdrawal gets a receipt, a
    cancellation the office confirmed says so, anything else is the school's
    own cancellation notice.
    """
    with transaction.atomic():
        cls = _locked_class(enrollment.activity_class_id)
        enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
        if enrollment.status == Enrollment.Status.CANCELLED:
            raise EnrollmentError("This registration is already cancelled.")
        held_seat = enrollment.status in Enrollment.SEAT_HOLDING_STATUSES
        enrollment.status = Enrollment.Status.CANCELLED
        enrollment.cancelled_at = timezone.now()
        enrollment.cancel_reason = reason
        if actor is not None and actor.is_authenticated and reason != enrollment.CancelReason.PARENT:
            enrollment.decided_by = actor
        enrollment.save()
        notifications.queue_event(
            _CANCEL_EVENTS.get(reason, Event.SUBSCRIPTION_CANCELLED), enrollment
        )
        if held_seat:
            waitlist_count = cls.enrollments.filter(
                status=Enrollment.Status.WAITLISTED
            ).count()
            if waitlist_count:
                notifications.queue_admin_event(
                    Event.ADMIN_SEAT_FREED, enrollment, waitlist_count=waitlist_count
                )
    return enrollment


def withdrawal_window_days():
    return SiteConfig.get().withdrawal_window_days


def parent_cancel(enrollment, actor):
    """A family wants out. Withdraw on the spot while that is allowed —
    nothing confirmed yet, or a confirmed place still inside the withdrawal
    window — otherwise file a cancellation request for the office.

    Returns the enrollment: CANCELLED when withdrawn, still ENROLLED with
    `cancel_requested_at` set when the office has to confirm. The caller reads
    the outcome from that, so a parent whose window closed between loading the
    page and clicking gets the request rather than an error.
    """
    with transaction.atomic():
        _locked_class(enrollment.activity_class_id)
        enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
        if enrollment.can_withdraw(withdrawal_window_days()):
            return cancel(enrollment, Enrollment.CancelReason.PARENT, actor=actor)
        return request_cancellation(enrollment, actor)


def request_cancellation(enrollment, actor):
    """Parent asks to cancel a confirmed place after the withdrawal window.

    The seat stays held and the child keeps attending; the office confirms
    (confirm_cancellation) or keeps the place (decline_cancellation).
    """
    with transaction.atomic():
        _locked_class(enrollment.activity_class_id)
        enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
        if enrollment.status == Enrollment.Status.CANCELLED:
            raise EnrollmentError("This registration is already cancelled.")
        if enrollment.status != Enrollment.Status.ENROLLED:
            raise EnrollmentError(
                "Only a confirmed place needs a cancellation request; anything else "
                "can simply be withdrawn."
            )
        if enrollment.cancellation_requested:
            raise EnrollmentError(
                "You have already asked to cancel this place; the office will confirm shortly."
            )
        enrollment.cancel_requested_at = timezone.now()
        enrollment.cancel_requested_by = actor if actor is not None and actor.is_authenticated else None
        enrollment.save()
        deadline = timezone.localtime(enrollment.withdrawal_deadline(withdrawal_window_days()))
        notifications.queue_event(Event.CANCELLATION_REQUESTED, enrollment)
        notifications.queue_admin_event(
            Event.ADMIN_CANCELLATION_REQUESTED,
            enrollment,
            withdrawal_deadline=deadline.strftime("%A %d %B"),
        )
    return enrollment


def _require_cancellation_request(enrollment):
    if enrollment.status != Enrollment.Status.ENROLLED or not enrollment.cancellation_requested:
        raise EnrollmentError("This place has no open cancellation request.")


def confirm_cancellation(enrollment, admin_user):
    """Admin confirms a family's cancellation request: the place ends and the
    family is told; a freed seat alerts the admins if anyone is waiting."""
    with transaction.atomic():
        _locked_class(enrollment.activity_class_id)
        enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
        _require_cancellation_request(enrollment)
        return cancel(enrollment, Enrollment.CancelReason.PARENT_REQUEST, actor=admin_user)


def decline_cancellation(enrollment, admin_user):
    """Admin keeps the place: the request is cleared and the family told."""
    with transaction.atomic():
        _locked_class(enrollment.activity_class_id)
        enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
        _require_cancellation_request(enrollment)
        enrollment.cancel_requested_at = None
        enrollment.cancel_requested_by = None
        enrollment.decided_by = admin_user
        enrollment.save()
        notifications.queue_event(Event.CANCELLATION_DECLINED, enrollment)
    return enrollment


def capacity_increased(activity_class):
    """After capacity is raised: alert admins if a waiting list exists."""
    with transaction.atomic():
        cls = _locked_class(activity_class.pk)
        waitlisted = cls.enrollments.waitlist_fifo()
        first = waitlisted.first()
        if first is not None:
            notifications.queue_admin_event(
                Event.ADMIN_SEAT_FREED, first, waitlist_count=waitlisted.count()
            )


def cancel_class(activity_class):
    """Cancel a class and every active enrollment in it, notifying families."""
    with transaction.atomic():
        cls = _locked_class(activity_class.pk)
        if cls.status == ActivityClass.Status.CANCELLED:
            return cls
        cls.status = ActivityClass.Status.CANCELLED
        cls.save(update_fields=["status"])
        active = cls.enrollments.select_for_update().filter(
            status__in=Enrollment.ACTIVE_STATUSES
        )
        now = timezone.now()
        for enrollment in active:
            enrollment.status = Enrollment.Status.CANCELLED
            enrollment.cancelled_at = now
            enrollment.cancel_reason = Enrollment.CancelReason.CLASS_CANCELLED
            enrollment.save()
            notifications.queue_event(Event.CLASS_CANCELLED, enrollment)
    return cls
