"""Queueing side of the notification pipeline (the transactional outbox).

Call these from inside the same database transaction as the state change they
announce: the Notification rows then commit atomically with the change, and
the run_notifier worker picks them up after commit. Nothing here talks to
SMTP or the WhatsApp API.
"""
import logging

from django.conf import settings
from django.template import Context, Template
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import SiteConfig, User

from .models import Broadcast, Event, Notification, NotificationTemplate

logger = logging.getLogger(__name__)


def _absolute(url_path):
    return f"{settings.SITE_URL}{url_path}"


def _render(template_string, context):
    return Template(template_string).render(Context(context))


def _get_template(event):
    template = NotificationTemplate.objects.filter(event=event).first()
    if template is None:
        logger.warning("No notification template configured for event %s", event)
        return None
    if not template.enabled:
        return None
    return template


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
    context.update(extra)
    if parent is not None:
        context["parent_name"] = parent.get_full_name() or parent.email
    if enrollment.offer_expires_at:
        local = timezone.localtime(enrollment.offer_expires_at)
        context["offer_expires_at"] = local.strftime("%A %d %B, %H:%M")
    return context


def _queue_for_user(user, event, template, context, enrollment=None, broadcast=None):
    """Create one Notification row per channel for a user, honoring opt-outs."""
    rows = []
    common = dict(
        recipient=user,
        event=event,
        enrollment=enrollment,
        broadcast=broadcast,
        recipient_email=user.email,
        recipient_phone=user.phone_e164,
    )

    email_row = Notification(
        channel=Notification.Channel.EMAIL,
        rendered_subject=_render(template.email_subject, context),
        rendered_body=_render(template.email_body, context),
        **common,
    )
    if not user.notify_email:
        email_row.status = Notification.Status.SKIPPED
        email_row.skip_reason = "Email notifications disabled"
    else:
        email_row.next_attempt_at = timezone.now()
    rows.append(email_row)

    wa_row = Notification(
        channel=Notification.Channel.WHATSAPP,
        wa_template_name=template.wa_template_name,
        wa_language=template.wa_language,
        wa_params=[str(context.get(key, "")) for key in template.wa_param_order],
        **common,
    )
    if not user.notify_whatsapp:
        wa_row.status = Notification.Status.SKIPPED
        wa_row.skip_reason = "WhatsApp notifications disabled"
    elif not user.phone_e164:
        wa_row.status = Notification.Status.SKIPPED
        wa_row.skip_reason = "No phone number on profile"
    elif not template.wa_template_name:
        wa_row.status = Notification.Status.SKIPPED
        wa_row.skip_reason = "No approved WhatsApp template configured for this event"
    else:
        wa_row.next_attempt_at = timezone.now()
    rows.append(wa_row)

    Notification.objects.bulk_create(rows)
    return rows


def queue_event(event, enrollment, **extra_context):
    """Notify every guardian of the enrollment's child about an event."""
    template = _get_template(event)
    if template is None:
        return
    for guardian in enrollment.child.guardians.filter(is_active=True):
        context = enrollment_context(enrollment, parent=guardian, **extra_context)
        _queue_for_user(guardian, event, template, context, enrollment=enrollment)


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

    for admin in User.objects.filter(role=User.Role.ADMIN, is_active=True):
        context = enrollment_context(
            enrollment,
            parent=None,
            action_url=_absolute(action_path),
            **extra_context,
        )
        context["parent_name"] = ", ".join(
            str(g) for g in enrollment.child.guardians.all()
        )
        # Admin alerts are email-only: WhatsApp templates target parents.
        subject = _render(template.email_subject, context)
        body = _render(template.email_body, context)
        Notification.objects.create(
            recipient=admin,
            recipient_email=admin.email,
            channel=Notification.Channel.EMAIL,
            event=event,
            enrollment=enrollment,
            rendered_subject=subject,
            rendered_body=body,
            next_attempt_at=timezone.now(),
        )


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
    Notification.objects.create(
        recipient=None,
        recipient_email=invite.email,
        channel=Notification.Channel.EMAIL,
        event=Event.GUARDIAN_INVITE,
        rendered_subject=_render(template.email_subject, context),
        rendered_body=_render(template.email_body, context),
        next_attempt_at=timezone.now(),
    )


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
        .select_related("child", "activity_class")
        .prefetch_related("child__guardians")
    ):
        for guardian in enrollment.child.guardians.filter(is_active=True):
            recipients.setdefault(guardian.pk, (guardian, enrollment))

    for guardian, enrollment in recipients.values():
        context = enrollment_context(
            enrollment,
            parent=guardian,
            subject=broadcast.subject,
            body=broadcast.body,
        )
        _queue_for_user(
            guardian,
            Event.BROADCAST,
            template,
            context,
            enrollment=None,
            broadcast=broadcast,
        )

    broadcast.sent_at = timezone.now()
    broadcast.save(update_fields=["sent_at"])
    return len(recipients)
