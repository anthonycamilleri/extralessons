"""Delivery runs inline, right after the state change commits.

This is what replaces a fast polling cron: the scheduled job survives only as
a safety net for the things no user action can trigger (offer expiry, rows
stranded by a crash, retries that come due while the site is idle).
"""
import pytest
from django.core import mail
from django.db import transaction

from apps.enrollments import services as enrollment_services
from apps.enrollments.services import EnrollmentError
from apps.notifications import services as notification_services
from apps.notifications import worker
from apps.notifications.models import Broadcast, Notification

from .factories import ActivityClassFactory, AdminFactory, ChildFactory, UserFactory

# transaction=True on purpose: delivery hangs off transaction.on_commit, and
# the default django_db fixture rolls back instead of committing, so on_commit
# hooks would never fire and every test here would pass vacuously.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def templates(default_notification_templates):
    """transaction=True truncates the seeded templates; put them back."""


@pytest.fixture
def inline(settings):
    settings.NOTIFIER_INLINE_DELIVERY = True
    settings.NOTIFIER_INLINE_MAX_SECONDS = 20
    return settings


def pending():
    return Notification.objects.filter(status=Notification.Status.PENDING)


def test_registration_email_is_sent_without_running_the_worker(inline):
    enrollment_services.register(ChildFactory(parent=UserFactory()), ActivityClassFactory())

    assert not pending().exists()
    assert Notification.objects.filter(status=Notification.Status.SENT).exists()
    assert len(mail.outbox) >= 1


def test_nothing_is_sent_when_the_transaction_rolls_back(inline):
    """A rolled back state change must not announce itself."""
    activity_class = ActivityClassFactory(capacity=1)
    child = ChildFactory(parent=UserFactory())
    enrollment_services.register(child, activity_class)
    mail.outbox.clear()

    # Registering the same child twice is rejected inside the transaction.
    with pytest.raises(EnrollmentError):
        enrollment_services.register(child, activity_class)

    assert mail.outbox == []


def test_one_pass_per_transaction_however_many_rows_it_queues(inline, monkeypatch):
    """A transition queues rows for the parent and for the admin; that is one
    delivery pass, not one per row."""
    AdminFactory(notify_email=True)
    passes = []
    original = worker.drain
    monkeypatch.setattr(
        worker, "drain", lambda *a, **kw: (passes.append(1), original(*a, **kw))[1]
    )

    enrollment_services.register(ChildFactory(parent=UserFactory()), ActivityClassFactory())

    assert Notification.objects.count() > 1, "test needs a multi-row transition"
    assert len(passes) == 1


def test_a_later_transaction_still_schedules_after_a_rollback(inline):
    """The dedupe must not latch: a rolled back transaction leaves no residue."""
    activity_class = ActivityClassFactory(capacity=5)
    child = ChildFactory(parent=UserFactory())
    enrollment_services.register(child, activity_class)
    with pytest.raises(EnrollmentError):
        enrollment_services.register(child, activity_class)
    mail.outbox.clear()

    enrollment_services.register(ChildFactory(parent=UserFactory()), activity_class)

    assert len(mail.outbox) >= 1
    assert not pending().exists()


def test_a_broken_mail_server_does_not_break_the_request(inline, monkeypatch):
    """The state change is already committed; the row keeps the retry."""
    def explode(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(worker, "drain", explode)

    enrollment = enrollment_services.register(
        ChildFactory(parent=UserFactory()), ActivityClassFactory()
    )

    assert enrollment.pk is not None
    assert pending().exists(), "rows must survive for the sweep to retry"


def test_delivery_failure_leaves_the_row_retryable(inline, monkeypatch, settings):
    """An inline send that fails must land in exactly the state a failed worker
    send would have left — that equivalence is what makes inline safe."""
    settings.NOTIFICATION_CHANNELS = dict(
        settings.NOTIFICATION_CHANNELS,
        EMAIL="tests.test_inline_delivery.RefusingEmailAdapter",
    )

    enrollment_services.register(ChildFactory(parent=UserFactory()), ActivityClassFactory())

    row = Notification.objects.filter(channel=Notification.Channel.EMAIL).first()
    assert row.status == Notification.Status.PENDING
    assert row.attempts == 1
    assert "refused" in row.last_error


class RefusingEmailAdapter:
    def __init__(self, connection=None):
        pass

    def send(self, notification):
        from apps.notifications.channels.base import ChannelError

        raise ChannelError("mail server refused")


def test_a_large_broadcast_goes_out_in_one_pass(inline, settings):
    """150 families is the realistic ceiling here, and it must not need the
    scheduled job to finish."""
    activity_class = ActivityClassFactory(capacity=200)
    for _ in range(30):
        enrollment_services.approve_request(
            enrollment_services.register(
                ChildFactory(parent=UserFactory()), activity_class
            ),
            AdminFactory(),
        )
    mail.outbox.clear()

    notification_services.create_broadcast(
        AdminFactory(),
        Broadcast.Scope.ALL_CLASSES,
        subject="Sports day moved",
        body="It is now Thursday.",
    )

    assert not pending().exists()
    assert len(mail.outbox) >= 30


def test_the_budget_hands_leftovers_back_to_the_queue(inline, settings):
    """A pass that runs out of time must leave the rest claimable rather than
    dropping it."""
    settings.NOTIFIER_INLINE_MAX_SECONDS = 0
    settings.NOTIFIER_BATCH_SIZE = 1
    AdminFactory(notify_email=True)

    # One transaction, several rows: the parent's confirmation and the admin
    # alert. A batch size of 1 and a budget of 0 means the pass sends one and
    # stops.
    enrollment_services.register(ChildFactory(parent=UserFactory()), ActivityClassFactory())

    assert Notification.objects.count() > 1, "test needs a multi-row transition"
    remaining = pending().count()
    assert remaining, "an exhausted budget should leave rows behind"

    # Nothing is lost: they are still claimable, which is all the sweep needs.
    assert worker.drain() == remaining
    assert not pending().exists()


def test_inline_delivery_can_be_turned_off(settings):
    settings.NOTIFIER_INLINE_DELIVERY = False

    enrollment_services.register(ChildFactory(parent=UserFactory()), ActivityClassFactory())

    assert pending().exists()
    assert mail.outbox == []


def test_the_sweep_still_delivers_what_inline_left(inline, settings):
    settings.NOTIFIER_INLINE_DELIVERY = False
    enrollment_services.register(ChildFactory(parent=UserFactory()), ActivityClassFactory())

    worker.run_once()

    assert not pending().exists()
