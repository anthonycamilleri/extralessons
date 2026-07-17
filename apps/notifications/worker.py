"""Delivery loop internals for the run_notifier command.

Claiming uses SELECT ... FOR UPDATE SKIP LOCKED so several workers could run
side by side, though one is plenty for a school. Sending happens outside any
transaction so a slow SMTP/Meta call never holds a database lock.
"""
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .channels.base import ChannelError, get_adapter
from .models import Notification

logger = logging.getLogger(__name__)

STUCK_SENDING_MINUTES = 10


def claim_batch(batch_size=None):
    """Atomically move a batch of due PENDING rows to SENDING and return them."""
    batch_size = batch_size or settings.NOTIFIER_BATCH_SIZE
    now = timezone.now()
    with transaction.atomic():
        rows = list(
            Notification.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("recipient")
            .filter(status=Notification.Status.PENDING, next_attempt_at__lte=now)
            .order_by("id")[:batch_size]
        )
        ids = [row.pk for row in rows]
        if ids:
            # next_attempt_at doubles as the claim timestamp while SENDING,
            # so recover_stuck can spot crashed claims.
            Notification.objects.filter(pk__in=ids).update(
                status=Notification.Status.SENDING, next_attempt_at=now
            )
    return rows


def _skip_reason_at_send(notification):
    """Re-check consent just before sending: preferences may have changed
    between queue time and delivery (retries can delay a send by hours)."""
    recipient = notification.recipient
    if recipient is None:
        return None
    if notification.channel == Notification.Channel.EMAIL:
        if not recipient.notify_email:
            return "Email notifications disabled before delivery"
    else:
        if not recipient.notify_whatsapp:
            return "WhatsApp notifications disabled before delivery"
        if not recipient.phone_e164:
            return "Phone number removed before delivery"
    return None


def deliver(notification):
    """Send one notification and record the outcome. Returns the final status."""
    skip_reason = _skip_reason_at_send(notification)
    if skip_reason:
        notification.status = Notification.Status.SKIPPED
        notification.skip_reason = skip_reason
        notification.save(update_fields=["status", "skip_reason"])
        return notification.status

    adapter = get_adapter(notification.channel)
    try:
        message_id = adapter.send(notification)
    except ChannelError as exc:
        return _record_failure(notification, exc)
    except Exception as exc:  # adapter bug — treat as retryable
        logger.exception("Unexpected error sending notification %s", notification.pk)
        return _record_failure(notification, ChannelError(str(exc)))

    notification.status = Notification.Status.SENT
    notification.provider_message_id = message_id or ""
    notification.sent_at = timezone.now()
    notification.attempts += 1
    notification.save(
        update_fields=["status", "provider_message_id", "sent_at", "attempts"]
    )
    return notification.status


def _record_failure(notification, exc):
    notification.attempts += 1
    notification.last_error = str(exc)
    max_attempts = settings.NOTIFIER_MAX_ATTEMPTS
    if getattr(exc, "permanent", False) or notification.attempts >= max_attempts:
        notification.status = Notification.Status.FAILED
        notification.save(update_fields=["status", "attempts", "last_error"])
        logger.error(
            "Notification %s failed permanently after %s attempt(s): %s",
            notification.pk,
            notification.attempts,
            exc,
        )
    else:
        # Exponential backoff: 2, 4, 8, ... minutes.
        delay_minutes = 2 ** notification.attempts
        notification.status = Notification.Status.PENDING
        notification.next_attempt_at = timezone.now() + timezone.timedelta(
            minutes=delay_minutes
        )
        notification.save(
            update_fields=["status", "attempts", "last_error", "next_attempt_at"]
        )
        logger.warning(
            "Notification %s attempt %s failed (%s); retrying in %s min",
            notification.pk,
            notification.attempts,
            exc,
            delay_minutes,
        )
    return notification.status


def recover_stuck():
    """Return crashed SENDING rows to the queue (may rarely cause a duplicate
    send after a mid-send crash; acceptable for this domain)."""
    cutoff = timezone.now() - timezone.timedelta(minutes=STUCK_SENDING_MINUTES)
    return Notification.objects.filter(
        status=Notification.Status.SENDING, next_attempt_at__lt=cutoff
    ).update(status=Notification.Status.PENDING, next_attempt_at=timezone.now())


def run_once():
    """One worker cycle: expire offers, recover stuck rows, deliver a batch."""
    from apps.enrollments.services import expire_offers

    expired = expire_offers()
    if expired:
        logger.info("Expired %s waiting-list offer(s)", expired)
    recovered = recover_stuck()
    if recovered:
        logger.warning("Recovered %s stuck notification(s)", recovered)

    batch = claim_batch()
    for notification in batch:
        deliver(notification)
    return len(batch)
