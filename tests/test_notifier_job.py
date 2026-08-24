"""`run_notifier --drain` is the mode a scheduled Serverless Job runs in.

Its contract is narrow but load-bearing: work through the backlog rather than
one batch per cron tick, stop as soon as the queue is empty so the job stops
billing, and never exit non-zero over a single bad cycle.
"""
import pytest
from django.core.management import CommandError, call_command

from apps.enrollments import services as enrollment_services
from apps.notifications.models import Notification

from .factories import ActivityClassFactory, ChildFactory, UserFactory

pytestmark = pytest.mark.django_db


def queue_notifications(count):
    """Produce `count` pending rows by registering that many enrollments."""
    activity_class = ActivityClassFactory(capacity=count + 1)
    for _ in range(count):
        enrollment_services.register(ChildFactory(parent=UserFactory()), activity_class)
    return Notification.objects.filter(status=Notification.Status.PENDING).count()


def test_drain_clears_a_backlog_larger_than_one_batch(settings):
    settings.NOTIFIER_BATCH_SIZE = 2
    pending = queue_notifications(5)
    assert pending > settings.NOTIFIER_BATCH_SIZE, "test needs a multi-batch backlog"

    call_command("run_notifier", drain=True, max_seconds=30)

    assert not Notification.objects.filter(status=Notification.Status.PENDING).exists()


def test_drain_returns_immediately_on_an_empty_queue():
    """The cheap case: nothing queued, so the job exits instead of idling."""
    call_command("run_notifier", drain=True, max_seconds=30)


def test_drain_stops_at_its_time_budget(settings):
    """A budget of zero still does one cycle, then hands the rest to the next run."""
    settings.NOTIFIER_BATCH_SIZE = 1
    queue_notifications(3)

    call_command("run_notifier", drain=True, max_seconds=0)

    assert Notification.objects.filter(status=Notification.Status.PENDING).exists()


def test_a_crashing_cycle_does_not_fail_the_run(monkeypatch):
    """A job that exits non-zero on a transient error would page someone at 3am
    and hide the batches that did go out."""
    from apps.notifications.management.commands import run_notifier as command_module

    def explode():
        raise RuntimeError("SMTP unreachable")

    monkeypatch.setattr(command_module, "run_once", explode)

    call_command("run_notifier", drain=True, max_seconds=5)


def test_once_and_drain_are_mutually_exclusive():
    with pytest.raises(CommandError):
        call_command("run_notifier", once=True, drain=True)
