from django.conf import settings
from django.db import models
from django.db.models import Q


class EnrollmentQuerySet(models.QuerySet):
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
    """

    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested (awaiting confirmation)"
        ENROLLED = "ENROLLED", "Enrolled"
        WAITLISTED = "WAITLISTED", "On waiting list"
        OFFERED = "OFFERED", "Seat offered"
        CANCELLED = "CANCELLED", "Cancelled"

    class CancelReason(models.TextChoices):
        PARENT = "PARENT", "Cancelled by parent"
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
