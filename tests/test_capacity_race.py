"""Concurrency: two admins approving at once must never oversubscribe a class.

Offers are deliberately not capacity-gated (an administrator may hand out a
place in a full class), so only approvals race for seats."""
import threading

import pytest
from django.db import connection

from apps.enrollments import services
from apps.enrollments.models import Enrollment

from .factories import ActivityClassFactory, AdminFactory, ChildFactory

# The capacity mutex is a row lock, and SQLite has no SELECT ... FOR UPDATE —
# Django silently drops the clause there, so these tests would assert nothing.
# Run the suite against PostgreSQL (as CI does) to exercise them.
pytestmark = pytest.mark.skipif(
    not connection.features.has_select_for_update,
    reason="Row-level locking requires PostgreSQL; set DATABASE_URL to a Postgres server.",
)


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

