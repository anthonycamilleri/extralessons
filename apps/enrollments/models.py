import datetime

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .ages import outside_recommended_range


class EnrollmentQuerySet(models.QuerySet):
    def pending_for(self, user):
        """Requests awaiting this admin's review: the one number behind the
        badge in the admin header, the site nav and the Requests page."""
        from apps.catalog.models import ActivityClass

        return self.filter(
            status=Enrollment.Status.REQUESTED,
            activity_class__in=ActivityClass.objects.managed_by(user),
        )

    def cancellation_requests_for(self, user):
        """Confirmed places whose family has asked to leave, waiting for this
        admin to confirm (or keep the place)."""
        from apps.catalog.models import ActivityClass

        return self.filter(
            status=Enrollment.Status.ENROLLED,
            cancel_requested_at__isnull=False,
            activity_class__in=ActivityClass.objects.managed_by(user),
        )

    def desk_count(self, user):
        """Everything on this admin's desk: new requests plus cancellation
        requests. The one number behind every badge."""
        return self.pending_for(user).count() + self.cancellation_requests_for(user).count()

    def waitlist_fifo(self):
        """Waitlisted rows in first-come order (single definition of FIFO)."""
        return self.filter(status=Enrollment.Status.WAITLISTED).order_by(
            "waitlisted_at", "id"
        )


class Enrollment(models.Model):
    """A child's relationship with a class, across its whole lifecycle.

    State machine (all transitions via apps.enrollments.services):

        parent registers ──► REQUESTED ── admin approves ──► ENROLLED (seat free)
                                 │                        └► WAITLISTED (class full)
                                 └───── admin rejects ────► CANCELLED
        WAITLISTED ── admin offers seat ──► OFFERED ── parent confirms ──► ENROLLED
        OFFERED ── parent declines / offer expires ──► CANCELLED
        any active state ── parent withdraws / admin cancels / class cancelled ──► CANCELLED

    Leaving a class has two shapes, decided by the withdrawal window
    (SiteConfig.withdrawal_window_days, counted from registration):

        REQUESTED / WAITLISTED / OFFERED ── parent withdraws ──► CANCELLED (always)
        ENROLLED, inside the window ── parent withdraws ──► CANCELLED
        ENROLLED, after the window ── parent asks to cancel ──► cancel_requested_at set
            ── admin confirms ──► CANCELLED        ── admin keeps the place ──► cleared

    A cancellation request is not a status: the child keeps the seat, and
    attends, until the office has confirmed.
    """

    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested (awaiting confirmation)"
        ENROLLED = "ENROLLED", "Enrolled"
        WAITLISTED = "WAITLISTED", "On waiting list"
        OFFERED = "OFFERED", "Seat offered"
        CANCELLED = "CANCELLED", "Cancelled"

    class CancelReason(models.TextChoices):
        PARENT = "PARENT", "Withdrawn by parent"
        PARENT_REQUEST = "PARENT_REQUEST", "Cancelled at the family's request"
        ADMIN = "ADMIN", "Cancelled by school"
        REQUEST_REJECTED = "REQUEST_REJECTED", "Request not approved"
        CLASS_CANCELLED = "CLASS_CANCELLED", "Class cancelled"
        OFFER_EXPIRED = "OFFER_EXPIRED", "Offer expired"
        OFFER_DECLINED = "OFFER_DECLINED", "Offer declined"

    # Statuses that occupy one of the class's seats.
    SEAT_HOLDING_STATUSES = [Status.ENROLLED, Status.OFFERED]
    ACTIVE_STATUSES = [Status.REQUESTED, Status.ENROLLED, Status.WAITLISTED, Status.OFFERED]

    child = models.ForeignKey(
        "accounts.Child", on_delete=models.CASCADE, related_name="enrollments"
    )
    activity_class = models.ForeignKey(
        "catalog.ActivityClass", on_delete=models.CASCADE, related_name="enrollments"
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED)

    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    waitlisted_at = models.DateTimeField(null=True, blank=True)
    offered_at = models.DateTimeField(null=True, blank=True)
    offer_expires_at = models.DateTimeField(null=True, blank=True)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_enrollments",
        help_text="Admin who last approved/rejected/offered.",
    )
    cancel_reason = models.CharField(
        max_length=20, choices=CancelReason.choices, blank=True, default=""
    )
    promoted_from_waitlist = models.BooleanField(default=False)
    cancel_requested_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the family asked to cancel a confirmed place after the "
        "withdrawal window; cleared when an admin confirms or keeps the place.",
    )
    cancel_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancellation_requests",
    )
    terms_accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the parent ticked the terms-and-conditions box on the registration form.",
    )

    objects = EnrollmentQuerySet.as_manager()

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["child", "activity_class"],
                condition=~Q(status="CANCELLED"),
                name="uniq_active_enrollment",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "offer_expires_at"]),
        ]

    def __str__(self):
        return f"{self.child} → {self.activity_class} [{self.status}]"

    @property
    def age_outside_range(self):
        """The child's age at term start when it sits outside the class's
        recommended range, else None. The parent confirmed the warning at
        registration; this is how the school sees it on review."""
        return outside_recommended_range(self.activity_class, self.child)

    @property
    def cancellation_requested(self):
        return self.cancel_requested_at is not None

    def withdrawal_deadline(self, window_days):
        """The moment self-service withdrawal of a confirmed place closes.

        Counted from registration. A child who came off the waiting list gets
        the window from the day their place was confirmed instead: the wait
        should not eat into their two weeks.
        """
        anchor = self.created_at
        if self.promoted_from_waitlist and self.enrolled_at:
            anchor = self.enrolled_at
        return anchor + datetime.timedelta(days=window_days)

    def can_withdraw(self, window_days, now=None):
        """Whether the family can leave with immediate effect.

        Always, while nothing is confirmed (a request, a waiting-list entry, an
        offer): nobody has planned around the child yet. For a confirmed place,
        only until the deadline; after that they ask, and the office confirms.
        """
        if self.status not in self.ACTIVE_STATUSES:
            return False
        if self.status != self.Status.ENROLLED:
            return True
        return (now or timezone.now()) < self.withdrawal_deadline(window_days)

    def waitlist_position(self):
        """1-based FIFO position among waitlisted enrollments (guidance only)."""
        if self.status != self.Status.WAITLISTED:
            return None
        return (
            Enrollment.objects.filter(activity_class=self.activity_class)
            .waitlist_fifo()
            .filter(
                Q(waitlisted_at__lt=self.waitlisted_at)
                | Q(waitlisted_at=self.waitlisted_at, id__lt=self.id)
            )
            .count()
            + 1
        )


class Attendance(models.Model):
    session = models.ForeignKey(
        "catalog.ClassSession", on_delete=models.CASCADE, related_name="attendance"
    )
    child = models.ForeignKey(
        "accounts.Child", on_delete=models.CASCADE, related_name="attendance"
    )
    present = models.BooleanField(default=False)
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    marked_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["session", "child"], name="uniq_attendance"),
        ]

    def __str__(self):
        state = "present" if self.present else "absent"
        return f"{self.child} @ {self.session}: {state}"
