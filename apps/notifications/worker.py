"""Delivery internals for the outbox.

Two callers, same machinery:

  * `drain()` runs inline right after a state change commits
    (apps.notifications.services.schedule_delivery), so a parent sees their
    email in seconds rather than whenever a timer next fires.
  * `run_once()` is the scheduled safety net — it additionally expires stale
    offers and recovers rows stranded by a crash, neither of which any user
    action can trigger.

Claiming uses SELECT ... FOR UPDATE SKIP LOCKED so the two can overlap safely.
Sending happens outside any transaction so a slow SMTP/Meta call never holds a
database lock.
"""
import contextlib
import logging
import time

from django.conf import settings
from django.core import mail
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


def deliver(notification, adapter=None):
    """Send one notification and record the outcome. Returns the final status.

    `adapter` lets a batch reuse one adapter (and so one SMTP connection)
    across many rows; without it each call resolves its own.
    """
    skip_reason = _skip_reason_at_send(notification)
    if skip_reason:
        notification.status = Notification.Status.SKIPPED
        notification.skip_reason = skip_reason
        notification.save(update_fields=["status", "skip_reason"])
        return notification.status

    if adapter is None:
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


@contextlib.contextmanager
def delivery_session():
    """Yield a channel -> adapter resolver whose adapters live for the batch.

    The email adapter gets one open SMTP connection for the whole session. A
    fresh TLS handshake per message costs more than the message, so for a
    150-family broadcast this is the difference between seconds and minutes.
    """
    email_connection = mail.get_connection()
    adapters = {}

    def resolve(channel):
        if channel not in adapters:
            if channel == Notification.Channel.EMAIL:
                adapters[channel] = get_adapter(channel, connection=email_connection)
            else:
                adapters[channel] = get_adapter(channel)
        return adapters[channel]

    try:
        yield resolve
    finally:
        # Never let closing the connection mask a delivery outcome that is
        # already recorded in the database.
        with contextlib.suppress(Exception):
            email_connection.close()


def deliver_batch(batch, resolve=None):
    """Deliver an already-claimed batch. Returns how many rows were processed."""
    if not batch:
        return 0
    if resolve is not None:
        for notification in batch:
            deliver(notification, resolve(notification.channel))
        return len(batch)
    with delivery_session() as resolve:
        for notification in batch:
            deliver(notification, resolve(notification.channel))
    return len(batch)


def drain(max_seconds=None, max_rows=None):
    """Deliver due notifications until the queue is empty or a limit is hit.

    Delivery only — no offer expiry, no stuck-row recovery. Those are the
    scheduled job's business (see `run_once`); doing them here would put a
    class-locking sweep on the critical path of an ordinary page load.

    Returns the number of rows processed.
    """
    deadline = None if max_seconds is None else time.monotonic() + max_seconds
    processed = 0
    with delivery_session() as resolve:
        while True:
            remaining = None if max_rows is None else max_rows - processed
            if remaining is not None and remaining <= 0:
                break
            batch_size = settings.NOTIFIER_BATCH_SIZE
            if remaining is not None:
                batch_size = min(batch_size, remaining)

            batch = claim_batch(batch_size)
            if not batch:
                break
            processed += deliver_batch(batch, resolve)

            if deadline is not None and time.monotonic() >= deadline:
                logger.info(
                    "Delivery budget spent after %s row(s); the rest goes out on the "
                    "next state change or the scheduled sweep.",
                    processed,
                )
                break
    return processed


def run_once():
    """One scheduled cycle: expire offers, recover stuck rows, deliver a batch.

    The two sweeps here are the reason a timer still exists at all: an offer
    reaching its deadline and a row stranded mid-send by a crash are both
    things that happen when nothing else is happening, so no user action can
    stand in for them.
    """
    from apps.enrollments.services import expire_offers

    expired = expire_offers()
    if expired:
        logger.info("Expired %s waiting-list offer(s)", expired)
    recovered = recover_stuck()
    if recovered:
        logger.warning("Recovered %s stuck notification(s)", recovered)

    batch = claim_batch()
    return deliver_batch(batch)
