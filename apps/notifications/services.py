"""Queueing side of the notification pipeline (the transactional outbox).

Call these from inside the same database transaction as the state change they
announce: the Notification rows then commit atomically with the change, and
the run_notifier worker picks them up after commit. Nothing here talks to
SMTP or the WhatsApp API.
"""
import logging

from django.conf import settings
from django.db.models import Prefetch
from django.template import Context, Template
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import SiteConfig, User

from .models import Broadcast, Event, Notification, NotificationTemplate

logger = logging.getLogger(__name__)


def _absolute(url_path):
    return f"{settings.SITE_URL}{url_path}"


class _CompiledTemplate:
    """A NotificationTemplate with its email templates compiled once."""

    def __init__(self, row):
        self.row = row
        self._subject = Template(row.email_subject)
        self._body = Template(row.email_body)

    def render_subject(self, context):
        # autoescape off: these are plain-text emails, not HTML.
        return self._subject.render(Context(context, autoescape=False))

    def render_body(self, context):
        return self._body.render(Context(context, autoescape=False))

    def wa_params(self, context):
        return [str(context.get(key, "")) for key in self.row.wa_param_order]


def _get_template(event):
    row = NotificationTemplate.objects.filter(event=event).first()
    if row is None:
        logger.warning("No notification template configured for event %s", event)
        return None
    if not row.enabled:
        return None
    return _CompiledTemplate(row)


def base_context(**extra):
    context = {"school_name": SiteConfig.get().school_name}
    context.update(extra)
    return context


def enrollment_context(enrollment, parent=None, **extra):
    cls = enrollment.activity_class
    context = base_context(
        child_name=enrollment.child.full_name,
        class_title=cls.title,
        provider_name=cls.provider.name,
        schedule=cls.schedule_display,
        term_name=cls.term.name,
        location=cls.location,
        action_url=_absolute(reverse("parent_home")),
    )
    if parent is not None:
        context["parent_name"] = parent.get_full_name() or parent.email
    if enrollment.offer_expires_at:
        local = timezone.localtime(enrollment.offer_expires_at)
        context["offer_expires_at"] = local.strftime("%A %d %B, %H:%M")
    context.update(extra)
    return context


def _email_row(template, context, *, recipient=None, email="", **row_fields):
    """Build (not save) one email Notification with rendered content."""
    row = Notification(
        channel=Notification.Channel.EMAIL,
        recipient=recipient,
        recipient_email=email or (recipient.email if recipient else ""),
        rendered_subject=template.render_subject(context),
        rendered_body=template.render_body(context),
        **row_fields,
    )
    if recipient is not None and not recipient.notify_email:
        row.status = Notification.Status.SKIPPED
        row.skip_reason = "Email notifications disabled"
    else:
        row.next_attempt_at = timezone.now()
    return row


def _whatsapp_row(template, context, recipient, **row_fields):
    """Build (not save) one WhatsApp Notification with snapshot params."""
    row = Notification(
        channel=Notification.Channel.WHATSAPP,
        recipient=recipient,
        recipient_email=recipient.email,
        recipient_phone=recipient.phone_e164,
        wa_template_name=template.row.wa_template_name,
        wa_language=template.row.wa_language,
        wa_params=template.wa_params(context),
        **row_fields,
    )
    if not recipient.notify_whatsapp:
        row.status = Notification.Status.SKIPPED
        row.skip_reason = "WhatsApp notifications disabled"
    elif not recipient.phone_e164:
        row.status = Notification.Status.SKIPPED
        row.skip_reason = "No phone number on profile"
    elif not template.row.wa_template_name:
        row.status = Notification.Status.SKIPPED
        row.skip_reason = "No approved WhatsApp template configured for this event"
    else:
        row.next_attempt_at = timezone.now()
    return row


def _rows_for_user(user, template, context, **row_fields):
    return [
        _email_row(template, context, recipient=user, **row_fields),
        _whatsapp_row(template, context, user, **row_fields),
    ]


def queue_event(event, enrollment, **extra_context):
    """Notify every guardian of the enrollment's child about an event."""
    template = _get_template(event)
    if template is None:
        return
    rows = []
    for guardian in enrollment.child.guardians.filter(is_active=True):
        context = enrollment_context(enrollment, parent=guardian, **extra_context)
        rows += _rows_for_user(
            guardian, template, context, event=event, enrollment=enrollment
        )
    Notification.objects.bulk_create(rows)


def queue_admin_event(event, enrollment, **extra_context):
    """Email school admins about workflow events, honoring SiteConfig toggles."""
    config = SiteConfig.get()
    if event == Event.ADMIN_NEW_REQUEST and not config.notify_admins_new_request:
        return
    if event == Event.ADMIN_SEAT_FREED and not config.notify_admins_seat_freed:
        return
    template = _get_template(event)
    if template is None:
        return

    if event == Event.ADMIN_NEW_REQUEST:
        action_path = reverse("admintools_requests")
    else:
        action_path = reverse(
            "admintools_waitlist", kwargs={"class_id": enrollment.activity_class_id}
        )
    context = enrollment_context(
        enrollment,
        parent_name=", ".join(str(g) for g in enrollment.child.guardians.all()),
        action_url=_absolute(action_path),
        **extra_context,
    )
    rows = [
        # Admin alerts are email-only: WhatsApp templates target parents.
        _email_row(template, context, recipient=admin, event=event, enrollment=enrollment)
        for admin in User.objects.filter(role=User.Role.ADMIN, is_active=True)
    ]
    Notification.objects.bulk_create(rows)


def queue_guardian_invite(invite):
    """Invite email to an address that may not have an account yet."""
    template = _get_template(Event.GUARDIAN_INVITE)
    if template is None:
        return
    context = base_context(
        parent_name=invite.invited_by.get_full_name() or invite.invited_by.email,
        child_name=invite.child.full_name,
        action_url=_absolute(reverse("invite_landing", kwargs={"token": invite.token})),
    )
    _email_row(
        template, context, email=invite.email, event=Event.GUARDIAN_INVITE
    ).save()


def create_broadcast(sender, scope, subject, body, classes=None):
    """Create a Broadcast and queue it, atomically with its outbox rows.

    Returns (broadcast, family_count). Shared by the admin and provider
    composers so the send flow exists exactly once.
    """
    from django.db import transaction

    with transaction.atomic():
        broadcast = Broadcast.objects.create(
            sender=sender, scope=scope, subject=subject, body=body
        )
        if scope == Broadcast.Scope.SELECTED_CLASSES:
            broadcast.classes.set(classes)
        count = queue_broadcast(broadcast)
    return broadcast, count


def queue_broadcast(broadcast):
    """Fan a broadcast out to guardians of children active in the target classes."""
    from apps.catalog.models import ActivityClass
    from apps.enrollments.models import Enrollment

    template = _get_template(Event.BROADCAST)
    if template is None:
        return 0

    if broadcast.scope == Broadcast.Scope.ALL_CLASSES:
        classes = ActivityClass.objects.published()
    else:
        classes = broadcast.classes.all()

    recipients = {}
    for enrollment in (
        Enrollment.objects.filter(
            activity_class__in=classes, status__in=Enrollment.ACTIVE_STATUSES
        )
        .select_related("child")
        .prefetch_related(
            Prefetch("child__guardians", queryset=User.objects.filter(is_active=True))
        )
    ):
        for guardian in enrollment.child.guardians.all():
            recipients.setdefault(guardian.pk, guardian)

    # The broadcast context is deliberately class-agnostic: a guardian may be
    # in several targeted classes, so per-class fields would be arbitrary.
    rows = []
    for guardian in recipients.values():
        context = base_context(
            parent_name=guardian.get_full_name() or guardian.email,
            subject=broadcast.subject,
            body=broadcast.body,
            action_url=_absolute(reverse("parent_home")),
        )
        rows += _rows_for_user(
            guardian, template, context, event=Event.BROADCAST, broadcast=broadcast
        )
    Notification.objects.bulk_create(rows, batch_size=500)

    broadcast.sent_at = timezone.now()
    broadcast.save(update_fields=["sent_at"])
    return len(recipients)


def family_count_phrase(count):
    return f"{count} famil{'y' if count == 1 else 'ies'}"
