"""Concurrency: two admins approving at once must never oversubscribe a class."""
import threading

import pytest
from django.db import connection

from apps.enrollments import services
from apps.enrollments.models import Enrollment

from .factories import ActivityClassFactory, AdminFactory, ChildFactory


@pytest.mark.django_db(transaction=True)
def test_concurrent_approvals_never_oversubscribe():
    admin = AdminFactory()
    cls = ActivityClassFactory(capacity=1)
    e1 = services.register(ChildFactory(), cls)
    e2 = services.register(ChildFactory(), cls)

    barrier = threading.Barrier(2)
    errors = []

    def approve(enrollment):
        try:
            barrier.wait(timeout=5)
            services.approve_request(enrollment, admin)
        except Exception as exc:  # pragma: no cover - failure diagnostics
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=approve, args=(e,)) for e in (e1, e2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, errors
    statuses = sorted(
        Enrollment.objects.filter(pk__in=[e1.pk, e2.pk]).values_list("status", flat=True)
    )
    assert statuses == ["ENROLLED", "WAITLISTED"]


@pytest.mark.django_db(transaction=True)
def test_concurrent_offers_for_single_seat():
    admin = AdminFactory()
    cls = ActivityClassFactory(capacity=2)
    enrolled = services.approve_request(services.register(ChildFactory(), cls), admin)
    services.approve_request(services.register(ChildFactory(), cls), admin)
    w1 = services.approve_request(services.register(ChildFactory(), cls), admin)
    w2 = services.approve_request(services.register(ChildFactory(), cls), admin)
    services.cancel(enrolled, Enrollment.CancelReason.PARENT)  # exactly one seat free

    barrier = threading.Barrier(2)
    outcomes = []

    def offer(enrollment):
        try:
            barrier.wait(timeout=5)
            services.offer_seat(enrollment, admin)
            outcomes.append("offered")
        except services.EnrollmentError:
            outcomes.append("blocked")
        finally:
            connection.close()

    threads = [threading.Thread(target=offer, args=(e,)) for e in (w1, w2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sorted(outcomes) == ["blocked", "offered"]
    offered_count = Enrollment.objects.filter(
        activity_class=cls, status=Enrollment.Status.OFFERED
    ).count()
    assert offered_count == 1
