from django.conf import settings
from django.db import models


class Event(models.TextChoices):
    """Every notification the system can send, parent-facing and admin-facing."""

    # Parent-facing
    ENROLLMENT_REQUESTED = "ENROLLMENT_REQUESTED", "Enrollment requested (receipt)"
    REGISTRATION_CONFIRMED = "REGISTRATION_CONFIRMED", "Registration confirmed"
    REQUEST_REJECTED = "REQUEST_REJECTED", "Request not approved"
    WAITLISTED = "WAITLISTED", "Added to waiting list"
    WAITLIST_OFFER = "WAITLIST_OFFER", "Seat offered from waiting list"
    OFFER_EXPIRED = "OFFER_EXPIRED", "Waiting-list offer expired"
    SUBSCRIPTION_CANCELLED = "SUBSCRIPTION_CANCELLED", "Enrollment cancelled"
    CLASS_CANCELLED = "CLASS_CANCELLED", "Class cancelled"
    GUARDIAN_INVITE = "GUARDIAN_INVITE", "Co-parent invitation"
    BROADCAST = "BROADCAST", "Announcement"
    # Admin-facing (email only)
    ADMIN_NEW_REQUEST = "ADMIN_NEW_REQUEST", "Admin: new enrollment request"
    ADMIN_SEAT_FREED = "ADMIN_SEAT_FREED", "Admin: seat freed"
    ADMIN_OFFER_LAPSED = "ADMIN_OFFER_LAPSED", "Admin: offer declined/expired"


class NotificationTemplate(models.Model):
    """Admin-editable content for each notification event.

    Email subject/body are Django template strings rendered with a context
    that includes: school_name, parent_name, child_name, class_title,
    provider_name, schedule, term_name, subject, body, action_url,
    offer_expires_at (where applicable).

    WhatsApp business-initiated messages must use templates pre-approved in
    Meta Business Manager: `wa_template_name` names the approved template and
    `wa_param_order` lists which context keys fill {{1}}..{{n}} in order.
    Leave `wa_template_name` empty to skip WhatsApp for this event.
    """

    event = models.CharField(max_length=30, choices=Event.choices, unique=True)
    enabled = models.BooleanField(default=True)
    email_subject = models.CharField(max_length=300)
    email_body = models.TextField()
    wa_template_name = models.CharField(max_length=100, blank=True)
    wa_language = models.CharField(max_length=10, default="en")
    wa_param_order = models.JSONField(
        default=list,
        blank=True,
        help_text='Context keys for the template placeholders, e.g. ["child_name", "class_title"]',
    )

    class Meta:
        ordering = ["event"]

    def __str__(self):
        return self.get_event_display()


class Broadcast(models.Model):
    class Scope(models.TextChoices):
        ALL_CLASSES = "ALL_CLASSES", "All published classes"
        SELECTED_CLASSES = "SELECTED_CLASSES", "Selected classes"

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="broadcasts"
    )
    scope = models.CharField(max_length=20, choices=Scope.choices)
    classes = models.ManyToManyField("catalog.ActivityClass", blank=True, related_name="broadcasts")
    subject = models.CharField(max_length=200)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} ({self.created_at:%Y-%m-%d})"


class Notification(models.Model):
    """Transactional outbox + delivery log: one row per recipient x channel."""

    class Channel(models.TextChoices):
        EMAIL = "EMAIL", "Email"
        WHATSAPP = "WHATSAPP", "WhatsApp"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SENDING = "SENDING", "Sending"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        SKIPPED = "SKIPPED", "Skipped"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
        help_text="Empty for messages to addresses without an account (e.g. invites).",
    )
    # Address snapshots taken at queue time, so later profile edits don't
    # change where a pending message goes.
    recipient_email = models.EmailField(blank=True)
    recipient_phone = models.CharField(max_length=20, blank=True)
    channel = models.CharField(max_length=10, choices=Channel.choices)
    event = models.CharField(max_length=30, choices=Event.choices)
    enrollment = models.ForeignKey(
        "enrollments.Enrollment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    broadcast = models.ForeignKey(
        Broadcast, on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications"
    )

    # Rendered snapshot at queue time — later edits never change what was sent.
    rendered_subject = models.CharField(max_length=300, blank=True)
    rendered_body = models.TextField(blank=True)
    wa_template_name = models.CharField(max_length=100, blank=True)
    wa_language = models.CharField(max_length=10, blank=True)
    wa_params = models.JSONField(default=list, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    skip_reason = models.CharField(max_length=100, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    provider_message_id = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"]),
        ]

    def __str__(self):
        return f"{self.event} → {self.recipient} via {self.channel} [{self.status}]"
